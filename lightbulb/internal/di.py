# -*- coding: utf-8 -*-
# Copyright © tandemdude 2023-present
#
# This file is part of Lightbulb.
#
# Lightbulb is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Lightbulb is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with Lightbulb. If not, see <https://www.gnu.org/licenses/>.
from __future__ import annotations

import contextvars
import inspect
import os
import typing as t

if t.TYPE_CHECKING:
    import svcs

AnyCallableT = t.TypeVar("AnyCallableT", bound=t.Callable[..., t.Any])
_di_container: contextvars.ContextVar[svcs.Container] = contextvars.ContextVar("_di_container")


def find_injectable_kwargs(
    func: t.Callable[..., t.Any], passed_args: int, passed_kwargs: t.Collection[str]
) -> t.Dict[str, t.Any]:
    """
    Given a function, parse the signature to discover which parameters are suitable for dependency injection.

    A parameter is suitable for dependency injection if:

    - It has a type annotation

    - It has no default value

    - It is not positional-only (injected parameters are always passed as a keyword argument)

    Args:
        func: The function to discover the dependency injection suitable parameters for.
        passed_args (:obj:`int`): The number of positional arguments passed to the function in this invocation.
        passed_kwargs (:obj:`~typing.Collection` [ :obj:`str` ]): The names of all the keyword arguments passed
            to the function in this invocation.

    Returns:
        :obj:`~typing.Dict` [ :obj:`str`, :obj:`~typing.Any` ]: Mapping of parameter name to parameter annotation
            value for parameters that are suitable for dependency injection.
    """
    parameters = inspect.signature(func, eval_str=True).parameters

    injectable_parameters: t.Dict[str, t.Any] = {}
    for parameter in [*parameters.values()][passed_args:]:
        # Injectable parameters MUST have an annotation and no default
        if (
            parameter.annotation is inspect.Parameter.empty
            or parameter.default is not inspect.Parameter.empty
            # Injecting positional only parameters is far too annoying
            or parameter.kind is inspect.Parameter.POSITIONAL_ONLY
            # If a kwarg has been passed then we don't want to replace it
            or parameter.name in passed_kwargs
        ):
            continue

        injectable_parameters[parameter.name] = parameter.annotation

    return injectable_parameters


class LazyInjecting:
    """
    Wrapper for a callable that implements dependency injection. When called, resolves the required
    dependencies and calls the original callable. Only supports asynchronous functions.

    You should generally never have to instantiate this yourself - you should instead use one of the
    decorators that applies this to the target automatically.
    """
    __slots__ = ("_func", "_processed", "_self", "__lb_cmd_invoke_method__")

    def __init__(
        self,
        func: t.Callable[..., t.Awaitable[t.Any]],
        self_: t.Any = None,
    ) -> None:
        self._func = func
        self._self: t.Any = self_

    def __get__(self, instance: t.Any, owner: t.Type[t.Any]) -> LazyInjecting:
        if instance is not None:
            return LazyInjecting(self._func, instance)
        return self

    async def __call__(self, *args: t.Any, **kwargs: t.Any) -> t.Any:
        new_kwargs: t.Dict[str, t.Any] = {}
        new_kwargs.update(kwargs)

        di_container: t.Optional[svcs.Container] = _di_container.get(None)
        if di_container is None:
            raise RuntimeError("cannot prepare dependency injection as client not yet populated")

        injectables = find_injectable_kwargs(self._func, len(args) + (self._self is not None), set(kwargs.keys()))

        for name, type in injectables.items():
            new_kwargs[name] = await di_container.aget(type)

        if self._self is not None:
            return await self._func(self._self, *args, **new_kwargs)
        return await self._func(*args, **new_kwargs)


if os.environ.get("LIGHTBULB_DI_DISABLED", "false").lower() == "true":

    class FakeLazyInjecting:
        __slots__ = ()

        # To disable DI we just replace the LazyInjecting class with one that does nothing
        # TODO - maybe look into doing this a different way in the future
        def __new__(cls, func: AnyCallableT, *args: t.Any, **kwargs: t.Any) -> AnyCallableT:
            return func

    LazyInjecting = FakeLazyInjecting  # type: ignore[reportAssignmentType]
