#!/usr/bin/env python
import os
import sys

from config.dotenv import load_local_env


def main() -> None:
    load_local_env()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
