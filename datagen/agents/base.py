"""
Base agent class defining the shared interface for all data generation agents.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from numpy.random import Generator

if TYPE_CHECKING:
    from datagen.state import UserState


class BaseAgent:
    """
    All agents share:
    - read access to UserState
    - a numpy Generator for stochastic draws
    - an accumulator for DB objects to be bulk-created later
    """

    def __init__(self, rng: Generator, use_llm: bool = False) -> None:
        self.rng = rng
        self.use_llm = use_llm

    def run(self, state: UserState, context: dict) -> dict:
        """
        Execute the agent's logic.

        Args:
            state: The mutable latent user state vector.
            context: Accumulated outputs from prior agents in the pipeline,
                     plus shared references (user ORM object, calendar window, etc.).

        Returns:
            Dict of new/modified outputs to merge into context.
        """
        raise NotImplementedError
