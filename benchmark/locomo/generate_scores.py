import json
import os.path
import sys

import pandas as pd

results_dir = sys.argv[1] if len(sys.argv) > 1 else "results"


# Load token usage data
def load_token_usage():
    """Load and calculate token usage from token1.json and token2.json"""
    try:
        with open(os.path.join(results_dir, "token1.json"), "r") as f:
            token1_data = json.load(f)
        with open(os.path.join(results_dir, "token2.json"), "r") as f:
            token2_data = json.load(f)

        # Calculate token differences
        token1_count = token1_data["token_count"]
        token2_count = token2_data["token_count"]
        
        token_usage = {
            "prompt_tokens": token2_count["prompt_tokens"] - token1_count["prompt_tokens"],
            "completion_tokens": token2_count["completion_tokens"] - token1_count["completion_tokens"],
            "total_tokens": token2_count["total_tokens"] - token1_count["total_tokens"],
            "cached_tokens": token2_count["cached_tokens"] - token1_count["cached_tokens"]
        }
        
        return token_usage
    except FileNotFoundError as e:
        print(f"Warning: Could not load token files: {e}")
        return None
    except Exception as e:
        print(f"Warning: Error processing token data: {e}")
        return None


# Load the evaluation metrics data
with open(os.path.join(results_dir, "evaluation_metrics.json"), "r") as f:
    data = json.load(f)

# Flatten the data into a list of question items
all_items = []
for key in data:
    all_items.extend(data[key])

# Convert to DataFrame
df = pd.DataFrame(all_items)

# Convert category to numeric type
df["category"] = pd.to_numeric(df["category"])

# Calculate mean scores by category
result = df.groupby("category").agg({"bleu_score": "mean", "f1_score": "mean", "llm_score": "mean"}).round(4)

# Add count of questions per category
result["count"] = df.groupby("category").size()

# Print the results
print("Mean Scores Per Category:")
print(result)

# Calculate overall means
overall_means = df.agg({"bleu_score": "mean", "f1_score": "mean", "llm_score": "mean"}).round(4)

print("\nOverall Mean Scores:")
print(overall_means)

# Load and display token usage information
token_usage = load_token_usage()
if token_usage:
    print("\nToken Usage During Evaluation:")
    print(f"Prompt tokens used: {token_usage['prompt_tokens']:,}")
    print(f"Completion tokens used: {token_usage['completion_tokens']:,}")
    print(f"Total tokens used: {token_usage['total_tokens']:,}")
    print(f"Cached tokens used: {token_usage['cached_tokens']:,}")
else:
    print("\nToken usage information not available.")
