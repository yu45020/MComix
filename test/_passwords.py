"""Explicit password answers shared by noninteractive archive tests."""

from collections import deque
import os
from typing import Any, Deque, Dict, List, Optional


class PasswordAnswers:
    """Answer only registered paths; unexpected prompts become test failures."""

    def __init__(self) -> None:
        self._answers: Dict[str, Deque[Optional[str]]] = {}
        self.calls: List[str] = []
        self.unexpected: List[str] = []
        self.real_dialogs = False

    @staticmethod
    def _key(archive: str) -> str:
        return os.path.normcase(os.path.abspath(archive))

    def add(self, archive: str, *answers: Optional[str]) -> None:
        self._answers[self._key(archive)] = deque(answers)

    def __call__(self, owner: Any) -> Optional[str]:
        archive = owner.archive
        self.calls.append(archive)
        answers = self._answers.get(self._key(archive))
        if answers:
            return answers.popleft()
        # A callback exception would strand BaseArchive._event waiters. Return
        # cancellation to unwind them, then fail explicitly at fixture teardown.
        self.unexpected.append(archive)
        return None

    def check(self) -> None:
        assert not self.unexpected, ('Unexpected archive password prompt: %s. '
                                     'Register a fixture answer or script the '
                                     'real dialog.' % ', '.join(self.unexpected))
        remaining = [path for path, answers in self._answers.items() if answers]
        assert not remaining, 'Unused archive password answers: %s' % remaining
