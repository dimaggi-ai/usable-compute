import os
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class ReproducibleBuildPy(build_py):
    def run(self):
        super().run()
        if not self.editable_mode:
            for output in self.get_outputs(include_bytecode=False):
                Path(output).chmod(0o644)


# Metadata is created in the backend process; source files stay untouched.
os.umask(0o022)
setup(cmdclass={'build_py': ReproducibleBuildPy})
