import os
import subprocess


def run_media_command(args, **kwargs):
    kwargs.setdefault("timeout", int(os.getenv("MEDIA_COMMAND_TIMEOUT_SECONDS", "900")))
    kwargs.setdefault("stderr", subprocess.PIPE)
    kwargs.setdefault("stdout", subprocess.DEVNULL)
    return subprocess.run(args, **kwargs)
