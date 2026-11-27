"""Subscription identity, ownership and main-thread dispatch regressions."""

import gc
import threading
import weakref
from typing import Any

import pytest

from mcomix.callback import Callback


class Source:
    @Callback
    def changed(self, value: Any = 0) -> Any:
        self.values.append(value)
        return value + 1

    def __init__(self) -> None:
        self.values = []


class Listener:
    def __init__(self, calls: Any) -> None:
        self.calls = calls

    def receive(self, value: Any = 0) -> None:
        self.calls.append(value)

    def other(self, value: Any = 0) -> None:
        self.calls.append(('other', value))


def test_bound_owner_is_weak_and_dead_listeners_are_removed() -> None:
    source = Source()
    calls = []
    listener = Listener(calls)
    ref = weakref.ref(listener)
    source.changed += listener.receive
    source.changed += listener.other
    source.changed(3)
    assert calls == [3, ('other', 3)]
    del listener
    gc.collect()
    assert ref() is None
    source.changed(4)
    assert calls == [3, ('other', 3)]


def test_weak_subscriptions_still_deduplicate_and_retain_plain_functions() -> None:
    source = Source()
    calls = []
    listener = Listener(calls)
    source.changed += listener.receive
    source.changed += listener.receive
    # Retain the CallbackList exactly as += does, without retaining the lambda.
    event = source.changed
    event += lambda value: calls.append(('lambda', value))
    gc.collect()
    source.changed(1)
    assert calls == [1, ('lambda', 1)]
    source.changed -= listener.receive
    source.changed(2)
    assert calls == [1, ('lambda', 1), ('lambda', 2)]


def test_falsey_source_still_receives_self() -> None:
    class FalseySource(Source):
        def __bool__(self) -> Any:
            return False

    source = FalseySource()
    assert source.changed(5) == 6
    assert source.values == [5]


def test_subscriber_classmethod_is_weak() -> None:
    source = Source()
    calls = []

    class Subscriber:
        @classmethod
        def receive(cls, value: Any) -> None:
            calls.append(value)

    ref = weakref.ref(Subscriber)
    source.changed += Subscriber.receive
    source.changed(1)
    del Subscriber
    gc.collect()
    assert ref() is None
    source.changed(2)
    assert calls == [1]


def test_self_removal_does_not_skip_the_next_listener() -> None:
    source = Source()
    calls = []

    def first(value: Any) -> Any:
        calls.append('first')
        source.changed.__isub__(first)

    source.changed += first
    source.changed += lambda value: calls.append('second')
    source.changed(0)
    source.changed(0)
    assert calls == ['first', 'second', 'second']


def test_removed_listener_skipped_and_new_listener_delayed() -> None:
    source = Source()
    calls = []

    def later(value: Any) -> Any:
        calls.append('later')

    def removed(value: Any) -> Any:
        calls.append('removed')

    def first(value: Any) -> Any:
        calls.append('first')
        source.changed.__isub__(removed)
        source.changed.__iadd__(later)

    source.changed += first
    source.changed += removed
    source.changed(0)
    assert calls == ['first']
    source.changed(0)
    assert calls == ['first', 'first', 'later']


def _call_from_worker(source: Source, monkeypatch: Any, value: Any) -> list:
    idle_calls = []
    monkeypatch.setattr('mcomix.callback.GLib.idle_add',
                        lambda *args: idle_calls.append(args))
    thread = threading.Thread(target=source.changed, args=(value,))
    thread.start()
    thread.join(3)
    assert not thread.is_alive()
    return idle_calls


def test_worker_call_queues_body_and_listener_on_main_thread(monkeypatch: Any) -> None:
    source = Source()
    calls = []
    source.changed += lambda value: calls.append((value, threading.current_thread()))
    idle_calls = _call_from_worker(source, monkeypatch, 7)
    assert source.values == calls == []
    assert len(idle_calls) == 1
    function, params = idle_calls.pop()
    assert function(params) is False
    assert source.values == [7]
    assert calls == [(7, threading.main_thread())]


def test_unsubscribe_before_idle_dispatch_cancels_listener(monkeypatch: Any) -> None:
    source = Source()
    calls = []
    listener = Listener(calls)
    source.changed += listener.receive
    idle_calls = _call_from_worker(source, monkeypatch, 1)
    source.changed -= listener.receive
    function, params = idle_calls.pop()
    assert function(params) is False
    assert source.values == [1]
    assert calls == []


def test_renamed_main_thread_does_not_requeue(monkeypatch: Any) -> None:
    source = Source()
    monkeypatch.setattr(threading.main_thread(), 'name', 'renamed-main')
    monkeypatch.setattr('mcomix.callback.GLib.idle_add',
                        lambda *args: pytest.fail('main call queued'))
    assert source.changed(10) == 11
