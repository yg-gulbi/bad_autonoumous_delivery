"""Compatibility entry point for the relocated ROS-free comparison tool."""

from research.evaluation.compare_methods import *  # noqa: F403
from research.evaluation.compare_methods import main


if __name__ == '__main__':
    main()
