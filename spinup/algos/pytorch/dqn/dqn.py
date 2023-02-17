from copy import deepcopy
from collections import deque

import numpy as np
import torch
from torch.optim import Adam
import gym
import time
import spinup.algos.pytorch.dqn.core as core
from spinup.utils.logx import EpochLogger


class ReplayBuffer:
    """
    A simple FIFO experience replay buffer for DQN agents.
    """

    def __init__(self, obs_dim, size):
        self.obs_buf = np.zeros(core.combined_shape(size, obs_dim), dtype=np.float32)
        self.obs2_buf = np.zeros(core.combined_shape(size, obs_dim), dtype=np.float32)
        self.act_buf = np.zeros(core.combined_shape(size, 1), dtype=np.float32)
        self.rew_buf = np.zeros(size, dtype=np.float32)
        self.done_buf = np.zeros(size, dtype=np.float32)
        self.ptr, self.size, self.max_size = 0, 0, size

    def store(self, obs, act, rew, next_obs, done):
        """
        Add a batch of transitions to the buffer.
        """
        self.obs_buf[self.ptr] = obs
        self.obs2_buf[self.ptr] = next_obs
        self.act_buf[self.ptr] = act
        self.rew_buf[self.ptr] = rew
        self.done_buf[self.ptr] = done
        self.ptr = (self.ptr + 1) % self.max_size
        self.size = min(self.size + 1, self.max_size)

    def sample_batch(self, batch_size=32):
        """
        Sample batch of transitions from the buffer.
        """
        idxs = np.random.randint(0, self.size, size=batch_size)
        batch = dict(
            obs=self.obs_buf[idxs],
            obs2=self.obs2_buf[idxs],
            act=self.act_buf[idxs],
            rew=self.rew_buf[idxs],
            done=self.done_buf[idxs],
        )
        return {k: torch.as_tensor(v, dtype=torch.float32) for k, v in batch.items()}

    def __len__(self):
        """
        Current size of the buffer.
        """
        return self.ptr


def dqn(
    env_fn,
    q_net=core.DQNQFunction,
    q_net_kwargs=dict(),
    seed=0,
    steps_per_epoch=5000,
    epochs=100,
    replay_size=int(1e5),
    gamma=0.99,
    polyak=0.995,
    q_lr=1e-3,
    batch_size=128,
    update_interval=50,
    num_test_episodes=2,
    max_ep_len=1000,
    grad_steps=1,
    max_grad_norm=10,
    exploration_start=1.0,
    exploration_end=0.05,
    exploration_decay=0.99995,
    logger_kwargs=dict(),
    save_freq=1,
):
    """
    Deep Q-Network Learning (DQN).
    Args:
        env_fn : A function which creates a copy of the environment.
            The environment must satisfy the OpenAI Gym API.
        q_net: The constructor method for a PyTorch Module with an ``act``
            method, and a ``q`` module. The ``act`` method should accept batches of
            observations as inputs, and ``q`` should accept a batch of observations
            as inputs. When called, these should return:
            ===========  ================  ======================================
            Call         Output Shape      Description
            ===========  ================  ======================================
            ``act``      (batch, act_dim)  | Numpy array of actions for each
                                           | observation.
            ``pi``       (batch, act_dim)  | Tensor containing actions from policy
                                           | given observations.
            ``q``        (batch,)          | Tensor containing the current estimate
                                           | of Q* for the provided observations
                                           | and actions. (Critical: make sure to
                                           | flatten this!)
            ===========  ================  ======================================
        q_net_kwargs (dict): Any kwargs appropriate for the ActorCritic object
            you provided to DDPG.
        seed (int): Seed for random number generators.
        steps_per_epoch (int): Number of steps of interaction (state-action pairs)
            for the agent and the environment in each epoch.
        epochs (int): Number of epochs to run and train agent.
        replay_size (int): Maximum length of replay buffer.
        gamma (float): Discount factor. (Always between 0 and 1.)
        polyak (float): Interpolation factor in polyak averaging for target
            networks. Target networks are updated towards main networks
            according to:
            .. math:: \\theta_{\\text{targ}} \\leftarrow
                \\rho \\theta_{\\text{targ}} + (1-\\rho) \\theta
            where :math:`\\rho` is polyak. (Always between 0 and 1, usually
            close to 1.)
        pi_lr (float): Learning rate for policy.
        q_lr (float): Learning rate for Q-networks.
        batch_size (int): Minibatch size for SGD.
        start_steps (int): Number of steps for uniform-random action selection,
            before running real policy. Helps exploration.
        update_after (int): Number of env interactions to collect before
            starting to do gradient descent updates. Ensures replay buffer
            is full enough for useful updates.
        update_every (int): Number of env interactions that should elapse
            between gradient descent updates. Note: Regardless of how long
            you wait between updates, the ratio of env steps to gradient steps
            is locked to 1.
        act_noise (float): Stddev for Gaussian exploration noise added to
            policy at training time. (At test time, no noise is added.)
        num_test_episodes (int): Number of episodes to test the deterministic
            policy at the end of each epoch.
        max_ep_len (int): Maximum length of trajectory / episode / rollout.
        grad_steps (int): Number of gradient steps at each update.
        max_grad_norm (float): Maximum vlaue for gradient clipping.
        logger_kwargs (dict): Keyword args for EpochLogger.
        save_freq (int): How often (in terms of gap between epochs) to save
            the current policy and value function.
    """
    logger = EpochLogger(**logger_kwargs)
    logger.save_config(locals())

    torch.manual_seed(seed)
    np.random.seed(seed)

    env, test_env = env_fn(), env_fn()
    obs_dim = env.observation_space.shape

    # Create q-network module and target network
    q_net = q_net(env.observation_space, env.action_space, **q_net_kwargs)
    q_net_targ = deepcopy(q_net)

    # Freeze target networks with respect to optimizers (only update via polyak averaging)
    for p in q_net_targ.parameters():
        p.requires_grad = False

    # Experience buffer
    replay_buffer = ReplayBuffer(obs_dim=obs_dim, size=replay_size)

    # Count variables
    var_counts = tuple(core.count_vars(module) for module in [q_net.q])
    logger.log(f"\nNumber of parameters: \t q: {var_counts[0]}\n")

    def exploration_schedule(exploration_rate):
        if exploration_rate > exploration_end:
            return exploration_rate * exploration_decay
        else:
            return exploration_end

    # Set up function for computing DQN loss
    def compute_loss_q(data):
        o, a, r, o2, d = (
            data["obs"],
            data["act"],
            data["rew"],
            data["obs2"],
            data["done"],
        )

        # Current Q-value estimates
        cur_q = q_net.q(o)
        # Extract Q-values for the actions in the buffer
        cur_q = torch.gather(cur_q, dim=1, index=a.long()).squeeze(-1)

        # Bellman backup for Q function
        with torch.no_grad():
            next_q = q_net_targ.q(o2)
            # Follow greedy policy: use the one with the highest value
            next_q, _ = next_q.max(dim=1)
            next_q = next_q.squeeze(-1)
            # 1-step TD target
            backup = r + gamma * (1 - d) * next_q

        # MSE loss against Bellman backup
        loss_q = ((cur_q - backup) ** 2).mean()

        # Useful info for logging
        loss_info = dict(QVals=cur_q.detach().numpy())

        return loss_q, loss_info

    # Set up optimizers for q-function
    q_optimizer = Adam(q_net.q.parameters(), lr=q_lr)

    # Set up model saving
    logger.setup_pytorch_saver(q_net)

    def update(data, grad_steps):
        for _ in range(grad_steps):
            # First run one gradient descent step for Q.
            loss_q, loss_info = compute_loss_q(data)

            q_optimizer.zero_grad()
            loss_q.backward()
            # Clip gradient norm
            torch.nn.utils.clip_grad_norm_(q_net.q.parameters(), max_grad_norm)
            q_optimizer.step()

        # Record things
        logger.store(LossQ=loss_q.item(), **loss_info)

        # Finally, update target networks by polyak averaging.
        with torch.no_grad():
            for p, p_targ in zip(q_net.parameters(), q_net_targ.parameters()):
                # NB: We use an in-place operations "mul_", "add_" to update target
                # params, as opposed to "mul" and "add", which would make new tensors.
                p_targ.data.mul_(polyak)
                p_targ.data.add_((1 - polyak) * p.data)

    def get_action(o, deterministic=False):
        if deterministic:
            return q_net.act(torch.as_tensor(o, dtype=torch.float32))
        if np.random.rand() < exploration_rate:
            return np.random.choice(env.action_space.n)
        return q_net.act(torch.as_tensor(o, dtype=torch.float32))

    def test_agent():
        for j in range(num_test_episodes):
            o, d, ep_ret, ep_len = test_env.reset(), False, 0, 0
            while not (d or (ep_len == max_ep_len)):
                # Take deterministic actions at test time (noise_scale=0)
                o, r, d, _ = test_env.step(get_action(o, deterministic=True))
                ep_ret += r
                ep_len += 1
            logger.store(TestEpRet=ep_ret, TestEpLen=ep_len)

    # Prepare for interaction with environment
    total_steps = steps_per_epoch * epochs
    start_time = time.time()
    o, ep_ret, ep_len = env.reset(), 0, 0
    exploration_rate = 1.0

    for t in range(total_steps):
        a = get_action(o)
        # Step the env
        o2, r, d, _ = env.step(a)

        ep_ret += r
        ep_len += 1

        d = False if ep_len == max_ep_len else d

        # Add experience to replay buffer
        replay_buffer.store(o, a, r, o2, d)

        # Exploration schedule
        exploration_rate = exploration_schedule(exploration_rate)

        # Super critical, easy to overlook step: make sure to update
        # most recent observation!
        o = o2

        # End of trajectory handling
        if d or (ep_len == max_ep_len):
            logger.store(EpRet=ep_ret, EpLen=ep_len)
            o, ep_ret, ep_len = env.reset(), 0, 0

        # Update handling
        if len(replay_buffer) >= batch_size and (t + 1) % update_interval == 0:
            for _ in range(update_interval):
                batch = replay_buffer.sample_batch(batch_size)
                update(data=batch, grad_steps=grad_steps)

        # End of epoch handling
        if (t + 1) % steps_per_epoch == 0:
            epoch = (t + 1) // steps_per_epoch

            # Save model
            if (epoch % save_freq == 0) or (epoch == epochs):
                logger.save_state({"env": env}, None)

            # Test the performance of the deterministic version of the agent.
            test_agent()

            # Log info about epoch
            logger.log_tabular("Epoch", epoch)
            logger.log_tabular("Epsilon", exploration_rate)
            logger.log_tabular("EpRet", with_min_and_max=True)
            logger.log_tabular("TestEpRet", with_min_and_max=True)
            logger.log_tabular("EpLen", average_only=True)
            logger.log_tabular("TestEpLen", average_only=True)
            logger.log_tabular("TotalEnvInteracts", t)
            logger.log_tabular("QVals", with_min_and_max=True)
            logger.log_tabular("LossQ", average_only=True)
            logger.log_tabular("Time", time.time() - start_time)
            logger.dump_tabular()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=str, default="LunarLander-v2")
    parser.add_argument("--hid", type=int, default=256)
    parser.add_argument("--l", type=int, default=2)
    parser.add_argument("--gamma", type=float, default=0.99)
    parser.add_argument("--seed", "-s", type=int, default=0)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--exp_name", type=str, default="coin")
    args = parser.parse_args()

    from spinup.utils.run_utils import setup_logger_kwargs

    logger_kwargs = setup_logger_kwargs(args.exp_name, args.seed)

    dqn(
        lambda: gym.make(args.env),
        q_net=core.DQNQFunction,
        q_net_kwargs=dict(hidden_sizes=[args.hid] * args.l),
        gamma=args.gamma,
        seed=args.seed,
        epochs=args.epochs,
        logger_kwargs=logger_kwargs,
    )
