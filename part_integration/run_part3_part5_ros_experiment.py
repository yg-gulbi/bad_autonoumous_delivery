#!/usr/bin/env python3
"""Part 3 mock 이동과 Part 5 MP4 침상 이탈 정지를 실제 ROS 토픽으로 검증한다."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import signal
import site
import subprocess
import sys
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String


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
        self._last_state_key = None
        self.mission_pub = self.create_publisher(String, '/ui/mission_req', 10)
        self.create_subscription(
            String, '/robot2/hospital_nav/command', self._command, 100)
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
        self._record('robot2_command', message.data)

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
        key = (
            robot.get('state'),
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
    while time.monotonic() < deadline:
        rclpy.spin_once(probe, timeout_sec=0.1)
        for name, process in processes.items():
            code = process.poll()
            if code is not None:
                raise RuntimeError(
                    f'{name} exited before {description} with code {code}')
        if predicate():
            return
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


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--domain-id', type=int, default=96)
    parser.add_argument('--startup-timeout', type=float, default=30.0)
    parser.add_argument('--signal-timeout', type=float, default=120.0)
    parser.add_argument('--mock-speed', type=float, default=0.2)
    parser.add_argument('--output-root')
    parser.add_argument('--video-path')
    parser.add_argument('--method-config')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    script_path = Path(__file__).resolve()
    workspace = script_path.parents[1]
    package_root = workspace / 'src' / 'part5_bed_exit'
    output_root = (
        Path(args.output_root).expanduser().resolve()
        if args.output_root else script_path.parent / 'results'
    )
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    run_dir = output_root / f'{stamp}_part3_part5'
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / 'ros_logs').mkdir()

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
        'criteria': {
            'mission_ack_ok': False,
            'move_observed': False,
            'part4_moving': False,
            'bed_exit_true_observed': False,
            'pause_observed': False,
            'safe_pause_with_bed_exit': False,
        },
    }
    processes = {}
    handles = {}
    probe = None
    error = None

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
        wait_for(
            probe,
            lambda: probe.count_subscribers('/ui/mission_req') >= 1 and
            probe.count_publishers('/fleet/state') >= 1,
            args.startup_timeout,
            'Part 3 mission subscriber and fleet state publisher',
            processes,
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

        wait_for(
            probe,
            lambda: any(
                ack.get('ref') == request_id and ack.get('ok') is True
                for ack in probe.acks
            ),
            args.startup_timeout,
            'successful mission acknowledgement',
            processes,
        )
        summary['criteria']['mission_ack_ok'] = True

        wait_for(
            probe,
            lambda: any(command.startswith('MOVE ') for command in probe.commands),
            args.startup_timeout,
            'robot2 MOVE command',
            processes,
        )
        summary['criteria']['move_observed'] = True

        def part4_is_moving():
            state = probe.last_state or {}
            robot = state.get('robots', {}).get('robot_a', {})
            return robot.get('part4_state') == 'MOVING'

        wait_for(
            probe,
            part4_is_moving,
            args.startup_timeout,
            'robot_a Part 4 MOVING state',
            processes,
        )
        summary['criteria']['part4_moving'] = True

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
        wait_for(
            probe,
            lambda: False in probe.bed_values,
            args.signal_timeout,
            'Part 5 SAFE(false) signal',
            processes,
        )

        wait_for(
            probe,
            lambda: True in probe.bed_values,
            args.signal_timeout,
            'Part 5 EXIT(true) signal',
            processes,
        )
        summary['criteria']['bed_exit_true_observed'] = True

        wait_for(
            probe,
            lambda: 'PAUSE' in probe.commands,
            args.startup_timeout,
            'robot2 PAUSE command',
            processes,
        )
        summary['criteria']['pause_observed'] = True

        def safe_paused_by_bed_exit():
            state = probe.last_state or {}
            robot = state.get('robots', {}).get('robot_a', {})
            return (
                robot.get('state') == 'SAFE_PAUSE' and
                'BED_EXIT' in robot.get('pause_causes', [])
            )

        wait_for(
            probe,
            safe_paused_by_bed_exit,
            args.startup_timeout,
            'robot_a SAFE_PAUSE with BED_EXIT cause',
            processes,
        )
        summary['criteria']['safe_pause_with_bed_exit'] = True
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
        for process in reversed(tuple(processes.values())):
            stop_process(process)
        for handle in handles.values():
            handle.close()
        summary['finished_at'] = datetime.now().astimezone().isoformat()
        summary['process_exit_codes'] = {
            name: process.poll() for name, process in processes.items()
        }
        write_json(run_dir / 'summary.json', summary)
        (run_dir / 'result.txt').write_text(
            f'{summary["status"]}\n'
            f'run_dir={run_dir}\n'
            f'error={error or "none"}\n',
            encoding='utf-8',
        )

    print(summary['status'])
    print(f'Artifacts: {run_dir}')
    if error:
        print(error, file=sys.stderr)
    return 0 if summary['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
