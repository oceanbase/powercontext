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

UV="${UV:-uv}"
UV_PYTHON="${UV_PYTHON:-3.11}"
UV_RUN=("${UV}" run --no-project --python "${UV_PYTHON}" --with-requirements requirements.txt python)

curl -s -X POST "$API_BASE_URL/reset_token_count"

# Record initial token count
curl -s "$API_BASE_URL/token_count" > "./$output_folder/token1.json"

"${UV_RUN[@]}" run_experiments.py --method add --output_folder "$output_folder"
"${UV_RUN[@]}" run_experiments.py --method search --output_folder "$output_folder" --top_k 30

# Record final token count
curl -s "$API_BASE_URL/token_count" > "./$output_folder/token2.json"

"${UV_RUN[@]}" evals.py --input_file "./$output_folder/results.json" --output_file "./$output_folder/evaluation_metrics.json"
echo '' >> "./$output_folder/evaluation.txt"
"${UV_RUN[@]}" generate_scores.py "$output_folder" >> "./$output_folder/evaluation.txt"

cat "./$output_folder/evaluation.txt"
