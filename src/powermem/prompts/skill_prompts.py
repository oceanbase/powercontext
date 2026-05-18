"""Skill distillation and merging prompts.

Provides prompt templates for:
1. Extracting reusable procedural skills (operation guides) from conversations
2. Merging similar skills by combining steps and pitfalls
"""

SKILL_DISTILL_PROMPT = """You are a Skill Extraction Expert. Analyze a conversation trajectory and extract reusable **procedural skills** — step-by-step operation guides for specific apps or scenarios.

A skill is a concrete, executable operation fragment: "how to do X on app Y". Each skill covers ONE sub-operation (e.g., login, paginated retrieval, data filtering), NOT an entire task.

RULES:
1. Extract skills ONLY when the conversation shows a multi-step workflow (≥2 steps) involving tools/APIs.
2. Each skill = ONE cohesive sub-operation on a specific app. A single conversation may yield MULTIPLE skills (e.g., login skill + pagination skill + filtering skill).
3. Steps must include concrete API calls with parameter names, but replace task-specific data values with placeholders.
4. Pitfalls MUST come from actual errors observed in the conversation (trial-and-error patterns).
5. If no reusable skill pattern exists, return {"skills": []}.
6. LANGUAGE: match the user's input language. NEVER translate.
7. SENSITIVE CONTENT: skip harmful/illegal content.

OUTPUT FORMAT — return ONLY valid JSON:
{"skills": [
  {
    "title": "≤20 chars, app + operation",
    "description": "one-line summary of what this skill does",
    "tags": ["app_name", "operation_type"],
    "procedure": {
      "prerequisites": ["required setup or context"],
      "steps": [
        {"index": 1, "action": "concrete API call with param names", "expected": "what success looks like", "note": "optional caveat"}
      ],
      "pitfalls": [
        {"error": "error message or symptom", "cause": "root cause", "fix": "how to resolve"}
      ]
    }
  }
]}

IMPORTANT: "tags" MUST be a JSON array of strings.

EXAMPLE:

Conversation where agent logs into Spotify, paginates through song library, gets song details — with errors along the way:
{"skills": [
  {
    "title": "Spotify登录",
    "description": "使用邮箱和密码登录Spotify获取access_token",
    "tags": ["spotify", "login"],
    "procedure": {
      "prerequisites": ["通过 supervisor 获取 email 和 password"],
      "steps": [
        {"index": 1, "action": "apis.spotify.login(username=email, password=pw)", "expected": "返回 {access_token, token_type}", "note": "username 是 email 不是 username"}
      ],
      "pitfalls": [
        {"error": "401 Invalid credentials", "cause": "用了错误的用户名格式", "fix": "用 profile email 作为 username"}
      ]
    }
  },
  {
    "title": "Spotify分页获取歌曲",
    "description": "分页遍历Spotify歌曲库获取所有歌曲",
    "tags": ["spotify", "pagination"],
    "procedure": {
      "prerequisites": ["已登录，持有 access_token"],
      "steps": [
        {"index": 1, "action": "循环 apis.spotify.show_song_library(access_token=token, page_index=N, page_limit=20)", "expected": "返回 song 对象列表", "note": "page_index 从 0 开始"},
        {"index": 2, "action": "当返回空列表时停止循环", "expected": "收集到所有歌曲", "note": "默认 page_limit=5，建议用 20"}
      ],
      "pitfalls": [
        {"error": "只返回 5 条结果", "cause": "未设置 page_limit，默认为 5", "fix": "设置 page_limit=20 并循环所有 page_index"},
        {"error": "401 Unauthorized", "cause": "未传 access_token", "fix": "所有需认证的 API 都要带 access_token 参数"}
      ]
    }
  }
]}

Now analyze the following conversation and extract skills:"""


SKILL_MERGE_PROMPT = """You are a Skill Dedup Judge. Given two skills, decide whether they describe the SAME operation and should be MERGED, or are DIFFERENT operations and should be kept SEPARATE.

MERGE when: they describe the same operation on the same app (e.g., both about "how to login to Spotify").
KEEP SEPARATE when: they describe different operations even if on the same app (e.g., "login" vs "pagination").

If MERGE — combine steps (union, deduplicate) and pitfalls (union, deduplicate by error). Take the more complete version of each step.
If SEPARATE — return skip.

RULES:
1. Preserve the original language.
2. Output ONLY valid JSON:
   MERGE:    {"action": "merge", "title": "≤20 chars", "description": "merged summary", "procedure": {"prerequisites": [...], "steps": [...], "pitfalls": [...]}}
   SEPARATE: {"action": "skip"}"""
