///|
/// Runnable demo of the PowerMem HTTP API from MoonBit.
///
/// Usage:
///   moon run --target native examples/moonbit
///
/// Environment variables:
///   POWERMEM_BASE_URL  Base URL of the server (default: http://localhost:8000)
///   POWERMEM_API_KEY   API key (optional, required only if server auth is on)

///|
async fn main {
  let banner = "=".repeat(60)
  println(banner)
  println("PowerMem MoonBit Client Example")
  println(banner)
  let client = init_client()
  run_examples(client)
  println("")
  println(banner)
  println("All examples completed.")
  println(banner)
}

///|
fn init_client() -> Client {
  let base_url = match @env.get_env_var("POWERMEM_BASE_URL") {
    Some(v) => v
    None => "http://localhost:8000"
  }
  let api_key = @env.get_env_var("POWERMEM_API_KEY")
  println("")
  println("Configuration:")
  println("  Base URL: \{base_url}")
  match api_key {
    Some(_) => println("  API Key:  (set)")
    None => println("  API Key:  (not set)")
  }
  match api_key {
    Some(key) => Client::new(base_url, api_key=key)
    None => Client::new(base_url)
  }
}

///|
async fn run_examples(client : Client) -> Unit {
  let user_id = "moonbit-example-user"
  let agent_id = "moonbit-example-agent"

  example_health(client)
  example_system_status(client)
  let created = example_create(client, user_id~, agent_id~)
  example_list(client, user_id~)
  example_search(client, user_id~, agent_id~)
  match created {
    Some(memories) if memories.length() > 0 => {
      let first = memories[0]
      example_update(client, memory_id=first.id)
      example_delete(client, memory_id=first.id)
    }
    _ => {
      section("6. Update Memory")
      println("  skipped (no memories created)")
      section("7. Delete Memory")
      println("  skipped (no memories created)")
    }
  }
}

///|
fn section(title : String) -> Unit {
  println("")
  println("-".repeat(40))
  println(title)
  println("-".repeat(40))
}

///|
async fn example_health(client : Client) -> Unit {
  section("1. Health Check")
  try {
    let h = client.health()
    println("  status: \{h.status}")
  } catch {
    e => println("  failed: " + @debug.to_string(e))
  }
}

///|
async fn example_system_status(client : Client) -> Unit {
  section("2. System Status")
  try {
    let s = client.system_status()
    println("  version: \{s.version}")
    println("  status:  \{s.status}")
    match s.storage_type {
      Some(v) => println("  storage: \{v}")
      None => ()
    }
    match s.llm_provider {
      Some(v) => println("  llm:     \{v}")
      None => ()
    }
  } catch {
    e => println("  failed: " + @debug.to_string(e))
  }
}

///|
async fn example_create(
  client : Client,
  user_id~ : String,
  agent_id~ : String,
) -> Array[Memory]? {
  section("3. Create Memory")
  let metadata = Json::object({
    "source": "moonbit-client-example".to_json(),
    "importance": "high".to_json(),
  })
  try
    client.create_memory(
      content="User likes coffee and goes to Starbucks every morning. They prefer latte.",
      user_id~,
      agent_id~,
      metadata~,
      infer=true,
    )
  catch {
    e => {
      println("  skipped: " + @debug.to_string(e))
      None
    }
  } noraise {
    memories => {
      println("  created \{memories.length()} memory(ies):")
      for i, m in memories {
        println("    [\{i + 1}] \{m.id}: \{m.content}")
      }
      Some(memories)
    }
  }
}

///|
async fn example_list(client : Client, user_id~ : String) -> Unit {
  section("4. List Memories")
  try {
    let list = client.list_memories(user_id~, limit=10)
    println("  total: \{list.total}, showing \{list.memories.length()}:")
    for i, m in list.memories {
      println("    [\{i + 1}] \{m.id}: \{truncate(m.content, 60)}")
    }
  } catch {
    e => println("  skipped: " + @debug.to_string(e))
  }
}

///|
async fn example_search(
  client : Client,
  user_id~ : String,
  agent_id~ : String,
) -> Unit {
  section("5. Search Memories")
  try {
    let resp = client.search_memories(
      query="What beverages does the user like?",
      user_id~,
      agent_id~,
      limit=5,
    )
    println("  query: \{resp.query}")
    println("  total: \{resp.total}")
    for i, r in resp.results {
      let score = match r.score {
        Some(s) => s.to_string()
        None => "-"
      }
      println(
        "    [\{i + 1}] \{r.memory_id} score=\{score}: \{truncate(r.content, 60)}",
      )
    }
  } catch {
    e => println("  skipped: " + @debug.to_string(e))
  }
}

///|
async fn example_update(client : Client, memory_id~ : String) -> Unit {
  section("6. Update Memory")
  try {
    let m = client.update_memory(
      memory_id~,
      content="User loves espresso and visits Starbucks daily",
    )
    println("  updated \{m.id}: \{m.content}")
    match m.updated_at {
      Some(ts) => println("  at: \{ts}")
      None => ()
    }
  } catch {
    e => println("  skipped: " + @debug.to_string(e))
  }
}

///|
async fn example_delete(client : Client, memory_id~ : String) -> Unit {
  section("7. Delete Memory")
  try {
    let d = client.delete_memory(memory_id~)
    println("  deleted \{d.memory_id}")
  } catch {
    e => println("  skipped: " + @debug.to_string(e))
  }
}

///|
/// Truncate for display, measured in Unicode characters rather than UTF-16
/// code units. Appends "..." when truncation happens. `offset_of_nth_char`
/// keeps surrogate pairs intact — raw offset slicing on a String can split
/// them and abort.
fn truncate(s : String, n : Int) -> String {
  if !s.char_length_ge(n + 1) {
    return s
  }
  let suffix = "..."
  let keep = n - suffix.char_length()
  if keep <= 0 {
    return suffix
  }
  match s.offset_of_nth_char(keep) {
    Some(off) => s.view(end_offset=off).to_string() + suffix
    None => s
  }
}
