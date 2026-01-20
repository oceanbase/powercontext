set -ex

# Load environment variables and export them
source .env

export OPENAI_API_KEY
export OPENAI_BASE_URL
export MODEL
export API_BASE_URL

output_folder=${1:-"results"}
if [ ! -d "$output_folder" ]; then
    mkdir "$output_folder"
fi

curl -s -X POST "$API_BASE_URL/reset_token_count"

# Record initial token count
curl -s "$API_BASE_URL/token_count" > "./$output_folder/token1.json"

python3 run_experiments.py --method add --output_folder "$output_folder"
python3 run_experiments.py --method search --output_folder "$output_folder" --top_k 30

# Record final token count
curl -s "$API_BASE_URL/token_count" > "./$output_folder/token2.json"

python3 evals.py --input_file "./$output_folder/results.json" --output_file "./$output_folder/evaluation_metrics.json"
echo '' >> "./$output_folder/evaluation.txt"
python3 generate_scores.py "$output_folder" >> "./$output_folder/evaluation.txt"

cat "./$output_folder/evaluation.txt"
