# LOCOMO Benchmark

> This project is originally forked from [mem0-evaluation](https://github.com/mem0ai/mem0/tree/6b5582f474cf56f73814348e195793f77b5a59dd/evaluation) in commit `6b5582f474cf56f73814348e195793f77b5a59dd`

## Dataset

The LOCOMO dataset used in our experiments can be seen from [locomo10.json](dataset/locomo10.json).

## Evaluation Metrics

We use several metrics to evaluate the performance of different memory techniques:

1. **BLEU Score**: Measures the similarity between the model's response and the ground truth.
2. **F1 Score**: Measures the harmonic mean of precision and recall.
3. **LLM Score**: A binary score (0 or 1) determined by an LLM judge evaluating the correctness of responses.
4. **Token Consumption**: Number of tokens required to generate final answer.
5. **Latency**: Time required during search and to generate response.

## Project Structure

```
.
├── dataset/
│   └── locomo10.json         # LOCOMO benchmark dataset
├── methods/
│   ├── add.py                # Memory addition methods
│   └── search.py             # Memory search methods
├── metrics/
│   ├── llm_judge.py          # LLM-based evaluation judge
│   └── utils.py              # Utility functions for metrics
├── results/                  # Directory for storing experiment results
├── evals.py                  # Evaluation framework
├── generate_scores.py        # Score generation script
├── prompts.py                # Prompt templates
├── requirements.txt          # Python dependencies
├── run_experiments.py        # Main experiment runner
└── run.sh                    # Shell script for running experiments
```

## License

[Apache License](LICENSE)
