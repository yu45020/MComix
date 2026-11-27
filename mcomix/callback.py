# -*- coding: utf-8 -*-

import traceback
import weakref
import threading
from typing import Any, Callable, Optional, Tuple
from gi.repository import GLib

from mcomix import log
from mcomix.i18n import _


class CallbackList(object):
    """Run a decorated function and its subscribers in the GTK main thread.

    Bound Python methods are weak subscriptions. Their owners must be kept
    alive by the application; ordinary functions and callable objects are
    retained strongly, so inline lambdas remain subscribed.
    """

    def __init__(self, obj: Any, function: Callable[..., Any]) -> None:
        self.__callbacks = []
        self.__object = obj
        self.__function = function

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if threading.current_thread() is threading.main_thread():
            if self.__object is not None:
                result = self.__function(self.__object, *args, **kwargs)
            else:
                result = self.__function(*args, **kwargs)
            self.__run_callbacks(*args, **kwargs)
            return result
        GLib.idle_add(self.__mainthread_call, (args, kwargs))

    def __iadd__(self, function: Callable[..., Any]) -> 'CallbackList':
        subscription = self.__get_function(function)
        if not any(self.__same_callback(item, subscription)
                   for item in self.__callbacks):
            self.__callbacks.append(subscription)
        return self

    def __isub__(self, function: Callable[..., Any]) -> 'CallbackList':
        subscription = self.__get_function(function)
        self.__callbacks = [item for item in self.__callbacks
                            if not self.__same_callback(item, subscription)]
        return self

    def __mainthread_call(self, params: tuple) -> bool:
        self(*params[0], **params[1])
        return False

    def __run_callbacks(self, *args: Any, **kwargs: Any) -> None:
        # A listener can unsubscribe itself/another listener, subscribe a new
        # listener or emit recursively. New listeners start at the next emit;
        # removed listeners must not be called from this snapshot.
        for subscription in self.__callbacks[:]:
            if not any(item is subscription for item in self.__callbacks):
                continue
            obj_ref, func = subscription
            if obj_ref is None:
                callback = func
            else:
                obj = obj_ref()
                if obj is None:
                    continue
                callback = func.__get__(obj)
            try:
                callback(*args, **kwargs)
            except Exception as e:
                log.error(_('! Callback %(function)r failed: %(error)s'),
                          {'function': callback, 'error': e})
                log.debug('Traceback:\n%s', traceback.format_exc())

    def __callback_deleted(self, obj_ref: weakref.ReferenceType) -> None:
        self.__callbacks = [item for item in self.__callbacks
                            if item[0] is not obj_ref]

    @staticmethod
    def __same_callback(left: tuple, right: tuple) -> bool:
        left_ref, left_func = left
        right_ref, right_func = right
        if left_ref is None or right_ref is None:
            return left_ref is right_ref and left_func == right_func
        # Weak-reference equality uses the referents' __eq__, which can make
        # distinct but equal subscribers incorrectly share a subscription.
        return left_ref() is right_ref() and left_func is right_func

    def __get_function(self, func: Callable[..., Any]) -> Tuple[
            Optional[weakref.ReferenceType], Callable[..., Any]]:
        obj = getattr(func, '__self__', None)
        function = getattr(func, '__func__', None)
        if obj is not None and function is not None:
            return weakref.ref(obj, self.__callback_deleted), function
        return None, func


class Callback(object):
    """Decorator class for using the CallbackList helper."""

    def __init__(self, function: Callable[..., Any]) -> None:
        self.__function = function

    def __get__(self, obj: Any, cls: Any) -> CallbackList:
        # += stores the CallbackList on that instance, shadowing this
        # descriptor. Class access remains callable as an unbound function.
        return CallbackList(obj, self.__function)

# vim: expandtab:sw=4:ts=4
