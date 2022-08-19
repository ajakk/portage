# Copyright 2012 Gentoo Foundation
# Distributed under the terms of the GNU General Public License v2

from .AsyncScheduler import AsyncScheduler
from typing import Any
from typing import Iterator
from mypy_extensions import NoReturn


class TaskScheduler(AsyncScheduler):

    """
    A simple way to handle scheduling of AbstractPollTask instances. Simply
    pass a task iterator into the constructor and call start(). Use the
    poll, wait, or addExitListener methods to be notified when all of the
    tasks have completed.
    """

    def __init__(self, task_iter: Iterator, **kwargs: Any) -> None:
        AsyncScheduler.__init__(self, **kwargs)
        self._task_iter = task_iter

    def _next_task(self) -> NoReturn:
        return next(self._task_iter)
