#!/usr/bin/env python3
"""Part 3 mock 이동과 Part 5 MP4 침상 이탈 정지를 실제 ROS 토픽으로 검증한다."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import signal
import site
import subprocess
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.serialization import deserialize_message
import rosbag2_py
from rosidl_runtime_py.utilities import get_message
from std_msgs.msg import Bool, String


RESULT_VERSION = '80805b4_v1_3'
PLANNED_TESTS = 7
BAG_FINAL_STATE_SAMPLES = 3
ROBOT_A_NS = 'robot3'
ROBOT_A_COMMAND_TOPIC = f'/{ROBOT_A_NS}/hospital_nav/command'
BAG_TOPICS = (
    '/ui/mission_req',
    '/ui/ack',
    ROBOT_A_COMMAND_TOPIC,
    '/fleet/state',
    '/patient/bed_exit',
)


class Tee:
    """진행 로그를 터미널과 실행 폴더에 동시에 기록한다."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
        return len(data)

    def flush(self):
        for stream in self.streams:
            stream.flush()


class IntegrationProbe(Node):
    def __init__(self):
        super().__init__('part3_part5_integration_probe')
        self.started = time.monotonic()
        self.events = []
        self.commands = []
        self.bed_values = []
        self.acks = []
        self.last_state = None
        self.state_transitions = []
        self.safe_pause_paused_samples = 0
        self._last_state_key = None
        self.mission_pub = self.create_publisher(String, '/ui/mission_req', 10)
        self.create_subscription(
            String, ROBOT_A_COMMAND_TOPIC, self._command, 100)
        self.create_subscription(Bool, '/patient/bed_exit', self._bed, 100)
        self.create_subscription(String, '/ui/ack', self._ack, 100)
        self.create_subscription(String, '/fleet/state', self._state, 100)

    def _record(self, kind, data):
        self.events.append({
            'elapsed_s': round(time.monotonic() - self.started, 6),
            'kind': kind,
            'data': data,
        })

    def _command(self, message):
        self.commands.append(message.data)
        self._record(f'{ROBOT_A_NS}_command', message.data)

    def _bed(self, message):
        self.bed_values.append(bool(message.data))
        self._record('bed_exit', bool(message.data))

    def _ack(self, message):
        try:
            data = json.loads(message.data)
        except json.JSONDecodeError:
            data = {'raw': message.data}
        self.acks.append(data)
        self._record('ui_ack', data)

    def _state(self, message):
        try:
            state = json.loads(message.data)
        except json.JSONDecodeError:
            self._record('invalid_fleet_state', message.data)
            return
        self.last_state = state
        robot = state.get('robots', {}).get('robot_a', {})
        bed = state.get('bed_exit', {})
        if (
            robot.get('state') == 'SAFE_PAUSE'
            and robot.get('part4_state') == 'PAUSED'
            and 'BED_EXIT' in robot.get('pause_causes', [])
        ):
            self.safe_pause_paused_samples += 1
        key = (
            robot.get('state'),
            robot.get('part4_state'),
            tuple(robot.get('pause_causes', [])),
            robot.get('paused_by'),
            bed.get('value'),
            bed.get('monitor', {}).get('state'),
        )
        if key != self._last_state_key:
            snapshot = {
                'robot_a': {
                    'state': robot.get('state'),
                    'part4_state': robot.get('part4_state'),
                    'mission_id': robot.get('mission_id'),
                    'pause_causes': robot.get('pause_causes', []),
                    'paused_by': robot.get('paused_by'),
                },
                'bed_exit': {
                    'value': bed.get('value'),
                    'monitor_state': bed.get('monitor', {}).get('state'),
                },
            }
            self.state_transitions.append(snapshot)
            self._record('fleet_state_transition', snapshot)
            self._last_state_key = key


def wait_for(probe, predicate, timeout_s, description, processes):
    deadline = time.monotonic() + timeout_s
    started = time.monotonic()
    next_progress = started + 5.0
    while time.monotonic() < deadline:
        rclpy.spin_once(probe, timeout_sec=0.1)
        for name, process in processes.items():
            code = process.poll()
            if code is not None:
                raise RuntimeError(
                    f'{name} exited before {description} with code {code}')
        if predicate():
            return
        now = time.monotonic()
        if now >= next_progress:
            print(
                f'[PROGRESS] waiting_for={description} '
                f'elapsed_s={now - started:.1f}/{timeout_s:.1f}',
                flush=True,
            )
            next_progress = now + 5.0
    raise TimeoutError(f'timed out after {timeout_s:.1f}s waiting for {description}')


def start_process(name, command, environment, run_dir, handles, processes):
    log_path = run_dir / f'{name}.log'
    handle = log_path.open('w', encoding='utf-8')
    process = subprocess.Popen(
        command,
        cwd=environment['PART_INTEGRATION_WORKSPACE'],
        env=environment,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    handles[name] = handle
    processes[name] = process
    print(
        f'[PROCESS START] name={name} pid={process.pid} log={log_path}',
        flush=True,
    )
    return process


def stop_process(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=8)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=4)


def write_json(path, data):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )


def read_bag_events(bag_dir):
    """완료된 MCAP bag을 시간순으로 읽어 검증에 필요한 값만 역직렬화한다."""
    reader = rosbag2_py.SequentialReader()
    reader.open(
        rosbag2_py.StorageOptions(uri=str(bag_dir), storage_id='mcap'),
        rosbag2_py.ConverterOptions('', ''),
    )
    topic_types = {
        item.name: item.type for item in reader.get_all_topics_and_types()
    }
    missing_topics = sorted(set(BAG_TOPICS) - set(topic_types))
    if missing_topics:
        raise AssertionError(f'rosbag missing topics: {missing_topics}')

    message_types = {
        topic: get_message(topic_types[topic]) for topic in BAG_TOPICS
    }
    events = []
    while reader.has_next():
        topic, serialized, timestamp_ns = reader.read_next()
        if topic not in message_types:
            continue
        message = deserialize_message(serialized, message_types[topic])
        if topic == '/patient/bed_exit':
            value = bool(message.data)
        elif topic in ('/ui/mission_req', '/ui/ack', '/fleet/state'):
            try:
                value = json.loads(message.data)
            except json.JSONDecodeError:
                value = {'raw': message.data}
        else:
            value = message.data
        events.append({
            'topic': topic,
            'timestamp_ns': int(timestamp_ns),
            'value': value,
        })
    return events


def evaluate_bag_evidence(events, request_id):
    """bag의 메시지와 기록 시각만으로 전체 안전정지 순서를 검증한다."""
    counts = {
        topic: sum(event['topic'] == topic for event in events)
        for topic in BAG_TOPICS
    }
    empty_topics = [topic for topic, count in counts.items() if count == 0]
    if empty_topics:
        raise AssertionError(f'rosbag topics without messages: {empty_topics}')

    def find(topic, predicate, after_ns=-1):
        for event in events:
            if (event['timestamp_ns'] > after_ns and event['topic'] == topic
                    and predicate(event['value'])):
                return event
        raise AssertionError(f'rosbag evidence not found: topic={topic} after_ns={after_ns}')

    mission = find(
        '/ui/mission_req',
        lambda value: isinstance(value, dict) and value.get('req_id') == request_id,
    )
    ack = find(
        '/ui/ack',
        lambda value: (
            isinstance(value, dict) and value.get('ref') == request_id
            and value.get('ok') is True
        ),
        mission['timestamp_ns'],
    )
    move = find(
        ROBOT_A_COMMAND_TOPIC,
        lambda value: isinstance(value, str) and value.startswith('MOVE '),
        mission['timestamp_ns'],
    )
    moving = find(
        '/fleet/state',
        lambda value: (
            isinstance(value, dict)
            and value.get('robots', {}).get('robot_a', {}).get('part4_state') == 'MOVING'
        ),
        move['timestamp_ns'],
    )
    safe = find(
        '/patient/bed_exit',
        lambda value: value is False,
        moving['timestamp_ns'],
    )
    bed_exit = find(
        '/patient/bed_exit',
        lambda value: value is True,
        safe['timestamp_ns'],
    )
    pause = find(
        ROBOT_A_COMMAND_TOPIC,
        lambda value: value == 'PAUSE',
        bed_exit['timestamp_ns'],
    )
    safe_pause = find(
        '/fleet/state',
        lambda value: (
            isinstance(value, dict)
            and value.get('robots', {}).get('robot_a', {}).get('state') == 'SAFE_PAUSE'
            and value.get('robots', {}).get('robot_a', {}).get('part4_state') == 'PAUSED'
            and 'BED_EXIT' in value.get('robots', {}).get('robot_a', {}).get(
                'pause_causes', [])
        ),
        bed_exit['timestamp_ns'],
    )

    ordered = {
        'mission_req': mission,
        'ack': ack,
        'move': move,
        'moving': moving,
        'bed_exit_false': safe,
        'bed_exit_true': bed_exit,
        'pause': pause,
        'safe_pause': safe_pause,
    }
    origin = mission['timestamp_ns']
    return {
        'message_count': len(events),
        'topic_counts': counts,
        'ordered_events': {
            name: {
                'offset_s': round((event['timestamp_ns'] - origin) / 1e9, 6),
                'value': event['value'],
            }
            for name, event in ordered.items()
        },
        'pause_before_safe_pause_in_recording': (
            pause['timestamp_ns'] <= safe_pause['timestamp_ns']
        ),
        'cross_topic_order_note': (
            'PAUSE와 SAFE_PAUSE는 서로 다른 토픽의 DDS 수신 시각이므로 둘 다 '
            'bed_exit_true 이후인지 검증하고 상호 기록 순서는 참고값으로 남긴다.'
        ),
    }


def print_observed(key, data):
    """상세 데이터는 결과 파일에 유지하고 콘솔에는 판정 핵심만 출력한다."""
    if key == 'mission_ack_ok':
        print(
            '[OBSERVED] '
            f'ref={data.get("ref")} ok={data.get("ok")} '
            f'mission_id={data.get("mission_id")} message={data.get("message")}',
            flush=True,
        )
        return
    if key == 'move_observed':
        print(
            f'[OBSERVED] move_commands={json.dumps(data.get("move_commands", []), ensure_ascii=False)}',
            flush=True,
        )
        return
    if key in ('part4_moving', 'safe_pause_with_bed_exit'):
        print(
            '[OBSERVED] '
            f'state={data.get("state")} part4_state={data.get("part4_state")} '
            f'mission_id={data.get("mission_id")} '
            f'pause_causes={json.dumps(data.get("pause_causes", []), ensure_ascii=False)} '
            f'paused_by={data.get("paused_by")}',
            flush=True,
        )
        return
    if key == 'bed_exit_true_observed':
        print(
            '[OBSERVED] '
            f'samples={data.get("sample_count", 0)} '
            f'false={data.get("false_count", 0)} '
            f'true={data.get("true_count", 0)} '
            f'first_false_index={data.get("false_index")}',
            flush=True,
        )
        return
    if key == 'pause_observed':
        print(
            f'[OBSERVED] commands={json.dumps(data.get("commands", []), ensure_ascii=False)}',
            flush=True,
        )
        return
    if key == 'rosbag_evidence':
        print(f'[OBSERVED] bag_messages={data.get("message_count", 0)}', flush=True)
        for topic, count in data.get('topic_counts', {}).items():
            print(f'[TOPIC] {topic} messages={count}', flush=True)

        labels = {
            'mission_req': 'MISSION',
            'ack': 'ACK',
            'move': 'MOVE',
            'moving': 'MOVING',
            'bed_exit_false': 'BED_EXIT_FALSE',
            'bed_exit_true': 'BED_EXIT_TRUE',
            'pause': 'PAUSE',
            'safe_pause': 'SAFE_PAUSE',
        }
        ordered = data.get('ordered_events', {})
        for index, (name, label) in enumerate(labels.items(), start=1):
            event = ordered.get(name)
            if event is None:
                continue
            value = event.get('value')
            if name == 'mission_req' and isinstance(value, dict):
                detail = (
                    f'req_id={value.get("req_id")} type={value.get("type")} '
                    f'route={value.get("pickup")}->{value.get("destination")}'
                )
            elif name == 'ack' and isinstance(value, dict):
                detail = (
                    f'ok={value.get("ok")} mission_id={value.get("mission_id")} '
                    f'ref={value.get("ref")}'
                )
            elif name in ('moving', 'safe_pause') and isinstance(value, dict):
                robot = value.get('robots', {}).get('robot_a', {})
                detail = (
                    f'state={robot.get("state")} part4_state={robot.get("part4_state")} '
                    f'pause_causes={json.dumps(robot.get("pause_causes", []), ensure_ascii=False)}'
                )
            else:
                detail = f'value={value}'
            print(
                f'[TRACE {index}/8] +{event.get("offset_s", 0):.6f}s '
                f'{label} {detail}',
                flush=True,
            )
        print(
            '[OBSERVED] '
            f'pause_before_safe_pause={data.get("pause_before_safe_pause_in_recording")}',
            flush=True,
        )
        print(
            '[DETAIL] full_evidence=bag_evidence.json raw_messages=rosbag/',
            flush=True,
        )
        return

    print(f'[OBSERVED] {json.dumps(data, ensure_ascii=False)}', flush=True)


def run_test(summary, index, key, expected, action, observed):
    """합격 기준 하나의 시작·기대값·관측값·종료 상태를 기록한다."""
    started = time.monotonic()
    result = {
        'index': index,
        'expected': expected,
        'status': 'RUNNING',
    }
    summary['test_results'][key] = result
    print(f'\n[TEST {index}/{PLANNED_TESTS} START] {key}', flush=True)
    print(f'[EXPECTED] {expected}', flush=True)
    try:
        action()
    except Exception as exc:
        result.update(
            status='FAIL',
            duration_s=round(time.monotonic() - started, 3),
            observed=observed(),
            error=f'{type(exc).__name__}: {exc}',
        )
        print(
            f'[TEST {index}/{PLANNED_TESTS} END] FAIL duration_s={result["duration_s"]}',
            flush=True,
        )
        print_observed(key, result['observed'])
        print(f'[REASON] {result["error"]}', flush=True)
        raise

    summary['criteria'][key] = True
    result.update(
        status='PASS',
        duration_s=round(time.monotonic() - started, 3),
        observed=observed(),
    )
    print(
        f'[TEST {index}/{PLANNED_TESTS} END] PASS duration_s={result["duration_s"]}',
        flush=True,
    )
    print_observed(key, result['observed'])


def append_experiment_name(run_dir, requested_name=None):
    """종료 후 선택한 실험명을 안전한 접미사로 결과 디렉터리에 추가한다."""
    input_name = requested_name
    if input_name is None and sys.stdin.isatty():
        input_name = input('\n실험명을 입력하세요(선택, Enter 시 생략): ').strip()
    if not input_name:
        return run_dir, None

    safe_name = re.sub(r'[^\w.-]+', '_', input_name, flags=re.UNICODE).strip('_.-')
    if not safe_name:
        print('[EXPERIMENT NAME] 유효한 문자가 없어 이름을 추가하지 않습니다.', flush=True)
        return run_dir, None
    target = run_dir.with_name(f'{run_dir.name}_{safe_name}')
    if target.exists():
        print(f'[EXPERIMENT NAME] 대상이 이미 존재합니다: {target}', flush=True)
        return run_dir, None
    try:
        run_dir.rename(target)
    except OSError as exc:
        print(f'[EXPERIMENT NAME] 디렉터리 이름 변경 실패: {exc}', flush=True)
        return run_dir, None
    print(f'[EXPERIMENT NAME] {safe_name}', flush=True)
    return target, safe_name


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--domain-id', type=int, default=96)
    parser.add_argument('--startup-timeout', type=float, default=30.0)
    parser.add_argument('--signal-timeout', type=float, default=120.0)
    parser.add_argument('--mock-speed', type=float, default=0.2)
    parser.add_argument('--output-root')
    parser.add_argument('--video-path')
    parser.add_argument('--method-config')
    parser.add_argument('--experiment-name')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    run_started = time.monotonic()
    script_path = Path(__file__).resolve()
    workspace = script_path.parents[2]
    package_root = workspace / 'src' / 'part5_bed_exit'
    output_root = (
        Path(args.output_root).expanduser().resolve()
        if args.output_root else script_path.parent / 'results' / RESULT_VERSION
    )
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    run_dir = output_root / f'{stamp}_part3_part5'
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / 'ros_logs').mkdir()
    bag_dir = run_dir / 'rosbag'
    execution_stream = (run_dir / 'execution.log').open('w', encoding='utf-8')
    sys.stdout = Tee(sys.stdout, execution_stream)
    sys.stderr = Tee(sys.stderr, execution_stream)

    video_path = Path(args.video_path).expanduser().resolve() if args.video_path else (
        package_root / 'research' / 'data' / 'videos' / 'test_input_sequence1.mp4')
    method_config = (
        Path(args.method_config).expanduser().resolve()
        if args.method_config else package_root / 'research' / 'configs' /
        'test_input_sequence1_methods_v1.json'
    )
    summary = {
        'status': 'FAIL',
        'started_at': datetime.now().astimezone().isoformat(),
        'workspace': str(workspace),
        'run_dir': str(run_dir),
        'domain_id': args.domain_id,
        'video_path': str(video_path),
        'method_config': str(method_config),
        'bag': str(bag_dir),
        'bag_topics': list(BAG_TOPICS),
        'test_results': {},
        'criteria': {
            'mission_ack_ok': False,
            'move_observed': False,
            'part4_moving': False,
            'bed_exit_true_observed': False,
            'pause_observed': False,
            'safe_pause_with_bed_exit': False,
            'rosbag_evidence': False,
        },
    }
    processes = {}
    handles = {}
    probe = None
    error = None

    print('[RUN START] part3_part5 integration', flush=True)
    print(
        f'[CONFIG] domain_id={args.domain_id} video={video_path} '
        f'method_config={method_config}',
        flush=True,
    )
    print(f'[PLAN] planned_tests={PLANNED_TESTS}', flush=True)
    print(f'[ARTIFACTS] {run_dir}', flush=True)

    try:
        if not shutil.which('ros2'):
            raise RuntimeError('ros2 not found; source ROS 2 and install/setup.bash first')
        for path in (video_path, method_config):
            if not path.is_file():
                raise FileNotFoundError(path)

        environment = os.environ.copy()
        environment.pop('ROS_DISCOVERY_SERVER', None)
        environment.pop('ROS_SUPER_CLIENT', None)
        environment['ROS_DOMAIN_ID'] = str(args.domain_id)
        environment['ROS_AUTOMATIC_DISCOVERY_RANGE'] = 'LOCALHOST'
        environment['ROS_LOG_DIR'] = str(run_dir / 'ros_logs')
        environment['PART_INTEGRATION_WORKSPACE'] = str(workspace)
        python_paths = [
            p for p in (site.getusersitepackages(), *site.getsitepackages())
            if Path(p).is_dir()
        ]
        if environment.get('PYTHONPATH'):
            python_paths.append(environment['PYTHONPATH'])
        environment['PYTHONPATH'] = os.pathsep.join(python_paths)

        os.environ.update({
            'ROS_DOMAIN_ID': str(args.domain_id),
            'ROS_AUTOMATIC_DISCOVERY_RANGE': 'LOCALHOST',
            'ROS_LOG_DIR': str(run_dir / 'ros_logs'),
        })
        os.environ.pop('ROS_DISCOVERY_SERVER', None)
        os.environ.pop('ROS_SUPER_CLIENT', None)

        start_process(
            'part3_launch',
            [
                'ros2', 'launch', 'part3_fleet_manager', 'fleet_mock.launch.py',
                f'db_path:={run_dir / "fleet.db"}',
                f'mock_speed:={args.mock_speed}',
                'assignment_mode:=ROLE',
                'robot_a_r1:=true',
                'robot_b_r1:=true',
            ],
            environment, run_dir, handles, processes,
        )

        rclpy.init(args=None)
        probe = IntegrationProbe()
        print('\n[SETUP START] Part 3 ROS interfaces', flush=True)
        wait_for(
            probe,
            lambda: probe.count_subscribers('/ui/mission_req') >= 1 and
            probe.count_publishers('/fleet/state') >= 1,
            args.startup_timeout,
            'Part 3 mission subscriber and fleet state publisher',
            processes,
        )
        print('[SETUP END] PASS mission_subscriber>=1 fleet_publisher>=1', flush=True)

        start_process(
            'rosbag_recorder',
            [
                'ros2', 'bag', 'record', '--storage', 'mcap',
                '-o', str(bag_dir), '--topics', *BAG_TOPICS,
            ],
            environment, run_dir, handles, processes,
        )
        def pre_mission_bag_subscriptions_ready():
            return (
                probe.count_subscribers('/ui/mission_req') >= 2
                and probe.count_subscribers('/ui/ack') >= 2
                and probe.count_subscribers(ROBOT_A_COMMAND_TOPIC) >= 3
                and probe.count_subscribers('/fleet/state') >= 2
            )

        print(
            '\n[BAG START] waiting for pre-mission topic subscriptions',
            flush=True,
        )
        wait_for(
            probe,
            pre_mission_bag_subscriptions_ready,
            args.startup_timeout,
            'rosbag subscriptions to mission, ack, command, and fleet state',
            processes,
        )
        bag_ready_counts = {
            topic: probe.count_subscribers(topic)
            for topic in BAG_TOPICS[:-1]
        }
        summary['bag_ready_subscriber_counts'] = bag_ready_counts
        print(
            f'[BAG READY] path={bag_dir} '
            f'pre_mission_subscribers={json.dumps(bag_ready_counts)} '
            'deferred_topic=/patient/bed_exit',
            flush=True,
        )

        request_id = f'part3-part5-{stamp}'
        mission = {
            'v': 1,
            'req_id': request_id,
            'type': 'PATIENT_TRANSPORT',
            'level': 2,
            'patient': {'name': 'integration-test', 'chart_no': 'P5-P3'},
            'pickup': 'C',
            'destination': 'A',
            'requested_by': 'automated-integration',
        }
        probe.mission_pub.publish(String(data=json.dumps(mission, ensure_ascii=False)))
        probe._record('mission_published', mission)

        run_test(
            summary,
            1,
            'mission_ack_ok',
            f'/ui/ack에 ref={request_id}, ok=true가 수신된다',
            lambda: wait_for(
                probe,
                lambda: any(
                    ack.get('ref') == request_id and ack.get('ok') is True
                    for ack in probe.acks
                ),
                args.startup_timeout,
                'successful mission acknowledgement',
                processes,
            ),
            lambda: next(
                (
                    ack for ack in reversed(probe.acks)
                    if ack.get('ref') == request_id
                ),
                {'received_acks': len(probe.acks)},
            ),
        )

        run_test(
            summary,
            2,
            'move_observed',
            f'{ROBOT_A_COMMAND_TOPIC}에서 MOVE 명령이 수신된다',
            lambda: wait_for(
                probe,
                lambda: any(command.startswith('MOVE ') for command in probe.commands),
                args.startup_timeout,
                f'{ROBOT_A_NS} MOVE command',
                processes,
            ),
            lambda: {
                'move_commands': [
                    command for command in probe.commands if command.startswith('MOVE ')
                ]
            },
        )

        def part4_is_moving():
            state = probe.last_state or {}
            robot = state.get('robots', {}).get('robot_a', {})
            return robot.get('part4_state') == 'MOVING'

        run_test(
            summary,
            3,
            'part4_moving',
            '/fleet/state의 robot_a.part4_state가 MOVING이 된다',
            lambda: wait_for(
                probe,
                part4_is_moving,
                args.startup_timeout,
                'robot_a Part 4 MOVING state',
                processes,
            ),
            lambda: (probe.last_state or {}).get('robots', {}).get('robot_a', {}),
        )

        start_process(
            'part5_mp4',
            [
                'ros2', 'run', 'part5_bed_exit', 'bed_exit', '--ros-args',
                '-p', f'method_config:={method_config}',
                '-p', f'video_path:={video_path}',
                '-p', 'loop_video:=true',
                '-p', 'capture_fps:=30.0',
                '-p', 'output_topic:=/patient/bed_exit',
                '-p', 'show:=false',
            ],
            environment, run_dir, handles, processes,
        )

        bed_sequence = {'false_index': None}

        def wait_for_bed_sequence():
            wait_for(
                probe,
                lambda: False in probe.bed_values,
                args.signal_timeout,
                'Part 5 SAFE(false) signal',
                processes,
            )
            bed_sequence['false_index'] = probe.bed_values.index(False)
            wait_for(
                probe,
                lambda: any(
                    value is True
                    for value in probe.bed_values[bed_sequence['false_index'] + 1:]
                ),
                args.signal_timeout,
                'Part 5 EXIT(true) signal after SAFE(false)',
                processes,
            )

        run_test(
            summary,
            4,
            'bed_exit_true_observed',
            '/patient/bed_exit에서 false 이후 true가 순서대로 수신된다',
            wait_for_bed_sequence,
            lambda: {
                'sample_count': len(probe.bed_values),
                'false_count': probe.bed_values.count(False),
                'true_count': probe.bed_values.count(True),
                'false_index': bed_sequence['false_index'],
            },
        )

        run_test(
            summary,
            5,
            'pause_observed',
            f'{ROBOT_A_COMMAND_TOPIC}에서 PAUSE가 수신된다',
            lambda: wait_for(
                probe,
                lambda: 'PAUSE' in probe.commands,
                args.startup_timeout,
                f'{ROBOT_A_NS} PAUSE command',
                processes,
            ),
            lambda: {'commands': probe.commands},
        )

        def safe_paused_by_bed_exit():
            state = probe.last_state or {}
            robot = state.get('robots', {}).get('robot_a', {})
            return (
                robot.get('state') == 'SAFE_PAUSE' and
                'BED_EXIT' in robot.get('pause_causes', []) and
                robot.get('part4_state') == 'PAUSED'
            )

        run_test(
            summary,
            6,
            'safe_pause_with_bed_exit',
            'robot_a.state=SAFE_PAUSE, pause_causes에 BED_EXIT 포함, part4_state=PAUSED',
            lambda: wait_for(
                probe,
                safe_paused_by_bed_exit,
                args.startup_timeout,
                'robot_a SAFE_PAUSE with BED_EXIT cause',
                processes,
            ),
            lambda: (probe.last_state or {}).get('robots', {}).get('robot_a', {}),
        )

        print(
            '\n[BAG SYNC START] waiting for repeated final safety state '
            f'samples={BAG_FINAL_STATE_SAMPLES}',
            flush=True,
        )
        wait_for(
            probe,
            lambda: probe.safe_pause_paused_samples >= BAG_FINAL_STATE_SAMPLES,
            args.startup_timeout,
            'repeated SAFE_PAUSE/PAUSED fleet states for rosbag capture',
            processes,
        )
        summary['bag_final_state_samples'] = probe.safe_pause_paused_samples
        print(
            '[BAG SYNC END] PASS '
            f'samples={probe.safe_pause_paused_samples}',
            flush=True,
        )

        recorder = processes['rosbag_recorder']
        print('\n[BAG STOP] flushing recorded messages', flush=True)
        stop_process(recorder)
        handles['rosbag_recorder'].flush()
        print(
            f'[BAG STOPPED] exit_code={recorder.poll()} path={bag_dir}',
            flush=True,
        )

        bag_evidence = {}
        summary['bag_evidence'] = bag_evidence

        def verify_rosbag_evidence():
            try:
                events = read_bag_events(bag_dir)
                bag_evidence.update({
                    'message_count': len(events),
                    'topic_counts': {
                        topic: sum(event['topic'] == topic for event in events)
                        for topic in BAG_TOPICS
                    },
                })
                bag_evidence.update(evaluate_bag_evidence(events, request_id))
            finally:
                write_json(run_dir / 'bag_evidence.json', bag_evidence)

        run_test(
            summary,
            7,
            'rosbag_evidence',
            'MCAP bag 재생 결과가 MISSION → ACK/MOVE → MOVING → '
            'false → true → PAUSE 및 SAFE_PAUSE(BED_EXIT) 인과 흐름을 증명한다',
            verify_rosbag_evidence,
            lambda: bag_evidence or {'bag': str(bag_dir)},
        )
        summary['status'] = 'PASS'
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
        summary['error'] = error
    finally:
        if probe is not None:
            summary['commands'] = probe.commands
            summary['bed_exit_samples'] = {
                'count': len(probe.bed_values),
                'false_count': probe.bed_values.count(False),
                'true_count': probe.bed_values.count(True),
            }
            summary['acks'] = probe.acks
            summary['state_transitions'] = probe.state_transitions
            with (run_dir / 'events.jsonl').open('w', encoding='utf-8') as stream:
                for event in probe.events:
                    stream.write(json.dumps(event, ensure_ascii=False) + '\n')
            probe.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        for name, process in reversed(tuple(processes.items())):
            stop_process(process)
            print(
                f'[PROCESS END] name={name} exit_code={process.poll()}',
                flush=True,
            )
        for handle in handles.values():
            handle.close()
        summary['finished_at'] = datetime.now().astimezone().isoformat()
        summary['duration_s'] = round(time.monotonic() - run_started, 3)
        summary['process_exit_codes'] = {
            name: process.poll() for name, process in processes.items()
        }

    run_dir, experiment_name = append_experiment_name(run_dir, args.experiment_name)
    summary['run_dir'] = str(run_dir)
    summary['bag'] = str(run_dir / 'rosbag')
    summary['experiment_name'] = experiment_name
    write_json(run_dir / 'summary.json', summary)
    (run_dir / 'result.txt').write_text(
        f'{summary["status"]}\n'
        f'run_dir={run_dir}\n'
        f'error={error or "none"}\n',
        encoding='utf-8',
    )

    passed = sum(result.get('status') == 'PASS'
                 for result in summary['test_results'].values())
    failed = sum(result.get('status') == 'FAIL'
                 for result in summary['test_results'].values())
    print('\n[FINAL QUANTITATIVE RESULTS]', flush=True)
    print(
        f'planned={PLANNED_TESTS} started={len(summary["test_results"])} '
        f'finished={passed + failed} passed={passed} failed={failed} '
        f'duration_s={summary["duration_s"]}',
        flush=True,
    )
    bag_evidence = summary.get('bag_evidence', {})
    print(
        f'bag_messages={bag_evidence.get("message_count", 0)} '
        f'bag_topic_counts={json.dumps(bag_evidence.get("topic_counts", {}), ensure_ascii=False)}',
        flush=True,
    )
    samples = summary.get('bed_exit_samples', {})
    print(
        f'bed_exit_samples={samples.get("count", 0)} '
        f'false_count={samples.get("false_count", 0)} '
        f'true_count={samples.get("true_count", 0)}',
        flush=True,
    )
    print(f'[RUN END] {summary["status"]}', flush=True)
    print(f'[ARTIFACTS] {run_dir}', flush=True)
    if error:
        print(error, file=sys.stderr)
    return 0 if summary['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
