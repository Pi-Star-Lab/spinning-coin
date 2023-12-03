# Author: Sheelabhadra Dey (sheelabhadra@gmail.com)
# Adapted from: https://github.com/mit-acl/gym-minigrid/blob/master/gym_minigrid/envs/fourrooms.py

import numpy as np

from gym import spaces
from gym_minigrid.minigrid import *
from spinup.environments.flat_minigrid import FlatMiniGridEnv


class NonStationaryFlatFourRoomsEnv(FlatMiniGridEnv):
    """
    2D grid world game environment with flattened image observations.
    """

    def __init__(self, grid_size=19, max_steps=100, agent_pos=None, goal_pos=None):
        self._agent_default_pos = agent_pos
        self._goal_default_pos = goal_pos
        self.episode_num = 0

        super().__init__(grid_size=grid_size, max_steps=max_steps)

    def reset(self):
        self.episode_num += 1

        if self.episode_num % 500 == 0 and (self.episode_num // 500) % 2 == 1:
            # Place a goal square in the bottom-right corner
            self.agent_pos = (1, 1)
            self.agent_dir = None
            self._goal_default_pos = (self.height - 2, self.width - 2)
        else:
            # Place a goal square in the bottom-right corner
            self.agent_pos = (1, self.width - 2)
            self.agent_dir = None

            self._goal_default_pos = (self.height - 2, 1)

        # Generate a new random grid at the start of each episode
        # To keep the same grid for each episode, call env.seed() with
        # the same seed before calling env.reset()
        self._gen_grid(self.width, self.height)

        # These fields should be defined by _gen_grid
        assert self.agent_pos is not None
        assert self.agent_dir is not None

        # Check that the agent doesn't overlap with an object
        start_cell = self.grid.get(*self.agent_pos)
        assert start_cell is None or start_cell.can_overlap()

        # Item picked up, being carried, initially nothing
        self.carrying = None

        # Step count since episode start
        self.step_count = 0

        # Return first observation
        obs = self.gen_obs()
        obs = obs.flatten() / 255.0

        return obs

    def _gen_grid(self, width, height):
        # Create the grid
        self.grid = Grid(width, height)

        # Generate the surrounding walls
        self.grid.horz_wall(0, 0)
        self.grid.horz_wall(0, height - 1)
        self.grid.vert_wall(0, 0)
        self.grid.vert_wall(width - 1, 0)

        room_w = width // 2
        room_h = height // 2

        # For each row of rooms
        for j in range(0, 2):

            # For each column
            for i in range(0, 2):
                xL = i * room_w
                yT = j * room_h
                xR = xL + room_w
                yB = yT + room_h

                # Bottom wall and door
                if i + 1 < 2:
                    self.grid.vert_wall(xR, yT, room_h)
                    pos = (xR, self._rand_int(yT + 1, yB))
                    self.grid.set(*pos, None)

                # Bottom wall and door
                if j + 1 < 2:
                    self.grid.horz_wall(xL, yB, room_w)
                    pos = (self._rand_int(xL + 1, xR), yB)
                    self.grid.set(*pos, None)

        # Randomize the player start position and orientation
        if self._agent_default_pos is not None:
            self.agent_pos = self._agent_default_pos
            self.grid.set(*self._agent_default_pos, None)
            self.agent_dir = self._rand_int(0, 4)  # assuming random start direction
        else:
            self.place_agent()

        if self._goal_default_pos is not None:
            goal = Goal()
            self.grid.set(*self._goal_default_pos, goal)
            goal.init_pos, goal.cur_pos = self._goal_default_pos
        else:
            self.place_obj(Goal())

        self.mission = "Reach the goal"

    def step(self, action):
        obs, reward, done, info = FlatMiniGridEnv.step(self, action)
        return obs, reward, done, info


class NonStationaryFlatFourRoomsEnv3x3(NonStationaryFlatFourRoomsEnv):
    def __init__(self) -> None:
        super().__init__(grid_size=7)


class NonStationaryFlatFourRoomsEnv7x7(NonStationaryFlatFourRoomsEnv):
    def __init__(self) -> None:
        super().__init__(grid_size=15)


class NonStationaryFlatFourRoomsEnv9x9(NonStationaryFlatFourRoomsEnv):
    def __init__(self) -> None:
        super().__init__(grid_size=19)
