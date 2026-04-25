"""Tests for compass/genetic_evolver.py — genetic algorithm strategy evolver."""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from compass.genetic_evolver import (
    DEFAULT_GENES, EvolutionResult, EvolverConfig, GeneticEvolver, Genome,
    crossover, decode_genome, evaluate_fitness, mutate, population_diversity,
    random_genome, tournament_select,
)

# ── Helpers ──────────────────────────────────────────────────────────────

def _trades(n=200, seed=42):
    rng = np.random.RandomState(seed)
    return pd.DataFrame({
        "entry_date": pd.bdate_range("2022-01-03", periods=n),
        "pnl": rng.normal(50, 200, n),
        "win": (rng.random(n) > 0.4).astype(int),
        "vix": rng.uniform(12, 35, n),
        "rsi_14": rng.uniform(20, 80, n),
        "iv_rank": rng.uniform(10, 90, n),
        "dte_at_entry": rng.randint(7, 45, n),
        "regime": rng.choice(["bull", "bear", "neutral", "high_vol"], n),
        "pred_prob": rng.uniform(0.3, 0.9, n),
        "contracts": rng.randint(1, 5, n),
    })

def _evolver(n=200, seed=42, **kw):
    # Small population + generations for fast tests
    defaults = dict(population_size=20, n_generations=5, seed=seed)
    defaults.update(kw)
    return GeneticEvolver(_trades(n, seed), EvolverConfig(**defaults))

# ── Genome operations ────────────────────────────────────────────────────

class TestDecodeGenome:
    def test_returns_dict(self):
        g = np.array([0.5] * len(DEFAULT_GENES))
        params = decode_genome(g, DEFAULT_GENES)
        assert isinstance(params, dict)
        assert len(params) == len(DEFAULT_GENES)
    def test_midpoint_values(self):
        g = np.array([0.5] * len(DEFAULT_GENES))
        params = decode_genome(g, DEFAULT_GENES)
        # ml_threshold: 0.40 + 0.5 * (0.95-0.40) = 0.675
        assert params["ml_threshold"] == pytest.approx(0.675)
    def test_min_values(self):
        g = np.zeros(len(DEFAULT_GENES))
        params = decode_genome(g, DEFAULT_GENES)
        assert params["ml_threshold"] == pytest.approx(0.40)
    def test_max_values(self):
        g = np.ones(len(DEFAULT_GENES))
        params = decode_genome(g, DEFAULT_GENES)
        assert params["ml_threshold"] == pytest.approx(0.95)
    def test_discrete_rounded(self):
        g = np.array([0.5] * len(DEFAULT_GENES))
        params = decode_genome(g, DEFAULT_GENES)
        assert params["max_dte"] == int(params["max_dte"])

class TestRandomGenome:
    def test_correct_length(self):
        rng = np.random.RandomState(42)
        g = random_genome(20, rng)
        assert len(g) == 20
    def test_range_01(self):
        rng = np.random.RandomState(42)
        g = random_genome(20, rng)
        assert np.all(g >= 0) and np.all(g <= 1)

class TestTournamentSelect:
    def test_returns_genome(self):
        pop = [Genome(np.random.rand(5), fitness=i) for i in range(10)]
        rng = np.random.RandomState(42)
        winner = tournament_select(pop, 3, rng)
        assert isinstance(winner, Genome)
    def test_selects_fitter(self):
        pop = [Genome(np.random.rand(5), fitness=i) for i in range(20)]
        rng = np.random.RandomState(42)
        wins = [tournament_select(pop, 5, rng).fitness for _ in range(50)]
        assert np.mean(wins) > 10  # should skew toward higher fitness

class TestCrossover:
    def test_uniform_returns_two(self):
        rng = np.random.RandomState(42)
        p1, p2 = np.random.rand(10), np.random.rand(10)
        c1, c2 = crossover(p1, p2, rng, "uniform")
        assert len(c1) == 10 and len(c2) == 10
    def test_arithmetic_blend(self):
        rng = np.random.RandomState(42)
        p1, p2 = np.zeros(5), np.ones(5)
        c1, c2 = crossover(p1, p2, rng, "arithmetic")
        assert np.all(c1 >= 0) and np.all(c1 <= 1)
    def test_children_in_range(self):
        rng = np.random.RandomState(42)
        p1, p2 = np.random.rand(10), np.random.rand(10)
        c1, c2 = crossover(p1, p2, rng, "uniform")
        assert np.all(c1 >= 0) and np.all(c1 <= 1)

class TestMutate:
    def test_stays_in_range(self):
        rng = np.random.RandomState(42)
        g = np.random.rand(20)
        m = mutate(g, 1.0, 0.5, rng)  # high rate + strength
        assert np.all(m >= 0) and np.all(m <= 1)
    def test_changes_values(self):
        rng = np.random.RandomState(42)
        g = np.full(10, 0.5)
        m = mutate(g, 1.0, 0.2, rng)
        assert not np.allclose(g, m)
    def test_zero_rate_no_change(self):
        rng = np.random.RandomState(42)
        g = np.full(10, 0.5)
        m = mutate(g, 0.0, 0.2, rng)
        assert np.allclose(g, m)

class TestDiversity:
    def test_identical_zero(self):
        pop = [Genome(np.full(5, 0.5)) for _ in range(10)]
        assert population_diversity(pop) == pytest.approx(0.0)
    def test_diverse_positive(self):
        pop = [Genome(np.random.rand(5)) for _ in range(10)]
        assert population_diversity(pop) > 0
    def test_single_genome(self):
        pop = [Genome(np.random.rand(5))]
        assert population_diversity(pop) == 0.0

# ── Fitness evaluation ───────────────────────────────────────────────────

class TestFitness:
    def test_returns_tuple(self):
        trades = _trades(100)
        mask = np.zeros(100, dtype=bool)
        mask[:60] = True
        params = decode_genome(np.full(len(DEFAULT_GENES), 0.5), DEFAULT_GENES)
        result = evaluate_fitness(params, trades, mask)
        assert len(result) == 6
    def test_oos_fitness_float(self):
        trades = _trades(100)
        mask = np.zeros(100, dtype=bool)
        mask[:60] = True
        params = decode_genome(np.full(len(DEFAULT_GENES), 0.5), DEFAULT_GENES)
        oos_fit, _, _, _, _, _ = evaluate_fitness(params, trades, mask)
        assert isinstance(oos_fit, float)
    def test_strict_filters_fewer_trades(self):
        trades = _trades(200)
        mask = np.zeros(200, dtype=bool)
        mask[:120] = True
        loose = {"ml_threshold": 0.3, "min_dte": 5, "max_dte": 50, "vix_floor": 10, "vix_ceiling": 50}
        strict = {"ml_threshold": 0.9, "min_dte": 25, "max_dte": 30, "vix_floor": 18, "vix_ceiling": 22}
        _, _, _, _, _, n_loose = evaluate_fitness(loose, trades, mask)
        _, _, _, _, _, n_strict = evaluate_fitness(strict, trades, mask)
        assert n_loose >= n_strict
    def test_short_data(self):
        trades = _trades(5)
        mask = np.array([True, True, True, False, False])
        params = decode_genome(np.full(len(DEFAULT_GENES), 0.5), DEFAULT_GENES)
        oos_fit, _, _, _, _, _ = evaluate_fitness(params, trades, mask)
        assert isinstance(oos_fit, float)

# ── Evolver ──────────────────────────────────────────────────────────────

class TestEvolver:
    def test_evolve_returns_result(self):
        e = _evolver(100, population_size=10, n_generations=3)
        r = e.evolve()
        assert isinstance(r, EvolutionResult)
    def test_best_has_params(self):
        e = _evolver(100, population_size=10, n_generations=3)
        r = e.evolve()
        assert len(r.best_params) > 0
    def test_fitness_history_length(self):
        e = _evolver(100, population_size=10, n_generations=5)
        r = e.evolve()
        assert len(r.fitness_history) == 5
    def test_diversity_history(self):
        e = _evolver(100, population_size=10, n_generations=3)
        r = e.evolve()
        assert len(r.diversity_history) == 3
    def test_convergence_gen_valid(self):
        e = _evolver(100, population_size=10, n_generations=5)
        r = e.evolve()
        assert 0 <= r.convergence_gen <= 5
    def test_oos_is_ratio(self):
        e = _evolver(200, population_size=15, n_generations=5)
        r = e.evolve()
        assert isinstance(r.oos_is_ratio, float)
    def test_best_genome_has_fitness(self):
        e = _evolver(200, population_size=15, n_generations=5)
        r = e.evolve()
        assert isinstance(r.best_genome.fitness, float)
    def test_elitism_preserves_best(self):
        e = _evolver(100, population_size=10, n_generations=5, elitism_count=2)
        r = e.evolve()
        # Best should improve or stay same across generations
        first = r.fitness_history[0]
        last = r.fitness_history[-1]
        # With elitism, final >= first (or close)
        assert last >= first - 0.1  # small tolerance
    def test_all_best_populated(self):
        e = _evolver(100, population_size=10, n_generations=5)
        r = e.evolve()
        assert len(r.all_best) == 5

# ── Edge cases ───────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_single_generation(self):
        e = _evolver(100, population_size=10, n_generations=1)
        r = e.evolve()
        assert len(r.fitness_history) == 1
    def test_small_population(self):
        e = _evolver(100, population_size=5, n_generations=3)
        r = e.evolve()
        assert isinstance(r, EvolutionResult)
    def test_high_mutation(self):
        e = _evolver(100, population_size=10, n_generations=3,
                     mutation_rate=1.0, mutation_strength=0.5)
        r = e.evolve()
        assert isinstance(r, EvolutionResult)
    def test_no_crossover(self):
        e = _evolver(100, population_size=10, n_generations=3, crossover_rate=0.0)
        r = e.evolve()
        assert isinstance(r, EvolutionResult)
