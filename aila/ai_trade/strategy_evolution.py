"""
STRATEGY EVOLUTION — strategy evolution through genetic algorithms.
Automatic optimization and creation of new strategies.
"""

import random
from dataclasses import dataclass, field

from ..utils.common import TTLCache


@dataclass
class StrategyGene:
    """Strategy genes for evolution."""

    name: str
    parameters: dict
    fitness: float = 0.0
    generation: int = 0
    parent_ids: list[str] = field(default_factory=list)
    mutations: list[str] = field(default_factory=list)


class StrategyEvolution:
    """Strategy evolution and optimization using genetic algorithms."""

    __slots__ = ['_claude_client', '_knowledge_base', '_cache', '_population', '_config', '_generation']

    def __init__(self, claude_client, knowledge_base):
        self._claude_client = claude_client
        self._knowledge_base = knowledge_base
        self._cache = TTLCache(default_ttl=300)
        self._population: list[StrategyGene] = []
        self._generation = 0
        self._config = {
            'population_size': 20,
            'elite_count': 4,
            'mutation_rate': 0.2,
            'crossover_rate': 0.7,
            'min_fitness_threshold': 0.3
        }

    async def initialize_population(self, base_strategies: list[dict]) -> list[StrategyGene]:
        """Initialize population from base strategies with random mutations."""
        self._population = []
        for strategy in base_strategies:
            gene = StrategyGene(
                name=strategy['name'],
                parameters=strategy['parameters'],
                generation=0
            )
            self._population.append(gene)

        while len(self._population) < self._config['population_size']:
            base = random.choice(base_strategies)
            mutated = await self._mutate_parameters(base['parameters'])
            gene = StrategyGene(
                name=f"{base['name']}_variant_{len(self._population)}",
                parameters=mutated,
                generation=0,
                mutations=['initial_random']
            )
            self._population.append(gene)
        return self._population

    async def evaluate_fitness(self, gene: StrategyGene, historical_data: list[dict]) -> float:
        """Evaluate strategy fitness on historical data using AI."""
        prompt = (
            "Evaluate strategy on historical data:\n\n"
            f"Strategy: {gene.name}\n"
            f"Parameters: {gene.parameters}\n"
            f"Historical data (last 100 candles): {historical_data[:10]}...\n\n"
            "Simulate trading and evaluate:\n"
            "1. Win Rate\n"
            "2. Profit Factor\n"
            "3. Max Drawdown\n"
            "4. Sharpe Ratio\n\n"
            "RETURN ONLY JSON:\n"
            "{\n"
            '    "win_rate": 0-100,\n'
            '    "profit_factor": number,\n'
            '    "max_drawdown_pct": number,\n'
            '    "sharpe_ratio": number,\n'
            '    "total_trades": number,\n'
            '    "total_profit_pct": number,\n'
            '    "fitness_score": 0-100,\n'
            '    "strengths": ["strength1", "strength2"],\n'
            '    "weaknesses": ["weakness1", "weakness2"]\n'
            "}"
        )
        result = await self._claude_client.analyze(prompt)
        fitness = result.get('fitness_score', 0) / 100
        gene.fitness = fitness
        return fitness

    async def evolve_generation(self, historical_data: list[dict]) -> list[StrategyGene]:
        """Evolve one generation: evaluate, select, crossover, mutate."""
        self._generation += 1

        # Evaluate unevaluated genes
        for gene in self._population:
            if gene.fitness == 0:
                await self.evaluate_fitness(gene, historical_data)

        # Sort by fitness
        self._population.sort(key=lambda x: x.fitness, reverse=True)

        # Keep elite
        elite = self._population[:self._config['elite_count']]
        new_population = list(elite)

        # Generate new population
        while len(new_population) < self._config['population_size']:
            if random.random() < self._config['crossover_rate']:
                parent1 = self._select_parent()
                parent2 = self._select_parent()
                child = await self._crossover(parent1, parent2)
            else:
                parent = self._select_parent()
                child = StrategyGene(
                    name=f"{parent.name}_gen{self._generation}",
                    parameters=parent.parameters.copy(),
                    generation=self._generation,
                    parent_ids=[parent.name]
                )

            if random.random() < self._config['mutation_rate']:
                child.parameters = await self._mutate_parameters(child.parameters)
                child.mutations.append(f'gen{self._generation}_mutation')

            new_population.append(child)

        self._population = new_population
        return self._population

    def _select_parent(self) -> StrategyGene:
        """Tournament selection for parent."""
        tournament_size = 3
        tournament = random.sample(
            self._population, min(tournament_size, len(self._population))
        )
        return max(tournament, key=lambda x: x.fitness)

    async def _crossover(self, parent1: StrategyGene, parent2: StrategyGene) -> StrategyGene:
        """Crossover two parents to create child."""
        child_params: dict = {}
        all_keys = set(parent1.parameters.keys()) | set(parent2.parameters.keys())

        for key in all_keys:
            if key in parent1.parameters and key in parent2.parameters:
                if isinstance(parent1.parameters[key], (int, float)):
                    child_params[key] = (parent1.parameters[key] + parent2.parameters[key]) / 2
                else:
                    child_params[key] = random.choice([
                        parent1.parameters[key], parent2.parameters[key]
                    ])
            elif key in parent1.parameters:
                child_params[key] = parent1.parameters[key]
            else:
                child_params[key] = parent2.parameters[key]

        return StrategyGene(
            name=f"cross_{parent1.name[:10]}_{parent2.name[:10]}_gen{self._generation}",
            parameters=child_params,
            generation=self._generation,
            parent_ids=[parent1.name, parent2.name]
        )

    async def _mutate_parameters(self, params: dict) -> dict:
        """Mutate strategy parameters randomly."""
        mutated = params.copy()
        for key, value in mutated.items():
            if random.random() < 0.3:
                if isinstance(value, int):
                    mutated[key] = value + random.randint(-2, 2)
                elif isinstance(value, float):
                    mutated[key] = value * random.uniform(0.8, 1.2)
        return mutated

    async def get_best_strategies(self, count: int = 5) -> list[StrategyGene]:
        """Get top strategies by fitness."""
        sorted_pop = sorted(self._population, key=lambda x: x.fitness, reverse=True)
        return sorted_pop[:count]

    async def suggest_new_strategy(self, market_conditions: dict) -> dict:
        """Suggest a new strategy based on market conditions using AI."""
        top_strategies = [
            {'name': g.name, 'fitness': g.fitness}
            for g in self._population[:3]
        ]
        prompt = (
            "Based on current market conditions suggest a new strategy:\n\n"
            f"Market conditions: {market_conditions}\n"
            f"Best current strategies: {top_strategies}\n\n"
            "RETURN ONLY JSON:\n"
            "{\n"
            '    "strategy_name": "name",\n'
            '    "strategy_type": "trend/reversal/breakout/scalp/swing",\n'
            '    "parameters": {"param1": value, "param2": value},\n'
            '    "entry_conditions": ["condition1", "condition2"],\n'
            '    "exit_conditions": ["condition1", "condition2"],\n'
            '    "best_market_conditions": ["condition1", "condition2"],\n'
            '    "expected_win_rate": number,\n'
            '    "expected_profit_factor": number,\n'
            '    "reasoning": "explanation"\n'
            "}"
        )
        return await self._claude_client.analyze(prompt)

    def get_evolution_stats(self) -> dict:
        """Get evolution statistics."""
        if not self._population:
            return {'generation': 0, 'population_size': 0}
        fitnesses = [g.fitness for g in self._population]
        return {
            'generation': self._generation,
            'population_size': len(self._population),
            'best_fitness': max(fitnesses),
            'avg_fitness': sum(fitnesses) / len(fitnesses),
            'worst_fitness': min(fitnesses),
            'elite_strategies': [
                g.name for g in self._population[:self._config['elite_count']]
            ]
        }
