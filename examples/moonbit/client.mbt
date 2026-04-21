///|
/// Minimal PowerMem HTTP client. Mirrors examples/go/client.go in shape —
/// inline, self-contained, no dependencies outside moonbitlang/core and
/// moonbitlang/async.

///|
priv suberror ApiError {
  Http(status_code~ : Int, body~ : String)
  Server(String)
  Parse(String)
  Request(String)
} derive(Debug)

///|
priv struct Client {
  base_url : String
  api_key : String?
}

///|
fn Client::new(base_url : String, api_key? : String) -> Client {
  { base_url, api_key }
}

// -----------------------------------------------------------------------------
// HTTP plumbing
// -----------------------------------------------------------------------------

///|
fn Client::headers(self : Client) -> Map[String, String] {
  let h : Map[String, String] = { "Content-Type": "application/json" }
  match self.api_key {
    Some(key) => h["X-API-Key"] = key
    None => ()
  }
  h
}

///|
/// Issue a request whose response envelope is `{success, data, message, ...}`.
/// Returns the unwrapped `data` field on success.
async fn Client::do_request(
  self : Client,
  meth : @http.RequestMethod,
  path : String,
  query : Array[(String, String)],
  body : Json?,
) -> Json raise ApiError {
  let full_path = "/api/v1" + path + encode_query(query)
  let http = @http.Client::new(self.base_url, headers=self.headers()) catch {
    e => raise Request("\{e}")
  }
  let resp = try {
    match (meth, body) {
      (Get, _) => http.get(full_path)
      (Post, Some(b)) => http.post(full_path, b.stringify())
      (Post, None) => http.post(full_path, "")
      (Put, Some(b)) => http.put(full_path, b.stringify())
      (Put, None) => http.put(full_path, "")
      (_, Some(b)) => {
        http.request(meth, full_path)
        http.write(b.stringify())
        http.end_request()
      }
      (_, None) => {
        http.request(meth, full_path)
        http.end_request()
      }
    }
  } catch {
    e => raise Request("\{e}")
  }
  let data = http.read_all() catch { e => raise Parse("\{e}") }
  http.close()
  let body_text = data.text() catch { e => raise Parse("\{e}") }
  if resp.code >= 400 {
    raise Http(status_code=resp.code, body=body_text)
  }
  if body_text == "" {
    return Json::object({})
  }
  let json = @json.parse(body_text) catch { e => raise Parse("\{e}") }
  unwrap_envelope(json)
}

///|
fn unwrap_envelope(json : Json) -> Json raise ApiError {
  match json {
    Object({ "success": False, "message": String(msg), .. }) =>
      raise Server(msg)
    Object({ "success": False, .. }) => raise Server("API request failed")
    Object({ "data": data, .. }) => data
    _ => json
  }
}

///|
fn[T : @json.FromJson] parse_as(json : Json) -> T raise ApiError {
  @json.from_json(json) catch {
    e => raise Parse("\{e}")
  }
}

///|
/// Percent-encode a value for use in a URL query string. Keeps the unreserved
/// set [A-Z a-z 0-9 - _ . ~] as-is and escapes everything else.
fn percent_encode(value : String) -> String {
  let bytes = @utf8.encode(value)
  let out = StringBuilder::new()
  for i in 0..<bytes.length() {
    let code = bytes.unsafe_get(i).to_int()
    let is_unreserved = (code >= 0x30 && code <= 0x39) ||
      (code >= 0x41 && code <= 0x5A) ||
      (code >= 0x61 && code <= 0x7A) ||
      code == 0x2D ||
      code == 0x2E ||
      code == 0x5F ||
      code == 0x7E
    if is_unreserved {
      out.write_char(Int::unsafe_to_char(code))
    } else {
      out.write_char('%')
      out.write_char(hex_digit(code / 16))
      out.write_char(hex_digit(code % 16))
    }
  }
  out.to_string()
}

///|
fn hex_digit(n : Int) -> Char {
  if n < 10 {
    Int::unsafe_to_char('0'.to_int() + n)
  } else {
    Int::unsafe_to_char('A'.to_int() + (n - 10))
  }
}

///|
fn encode_query(pairs : Array[(String, String)]) -> String {
  if pairs.is_empty() {
    return ""
  }
  let out = StringBuilder::new()
  out.write_char('?')
  for i, pair in pairs {
    if i > 0 {
      out.write_char('&')
    }
    out.write_string(percent_encode(pair.0))
    out.write_char('=')
    out.write_string(percent_encode(pair.1))
  }
  out.to_string()
}

///|
fn json_object(entries : Array[(String, Json?)]) -> Json {
  let map : Map[String, Json] = {}
  for entry in entries {
    match entry {
      (key, Some(value)) => map[key] = value
      _ => ()
    }
  }
  Json::object(map)
}

// -----------------------------------------------------------------------------
// System endpoints
// -----------------------------------------------------------------------------

///|
async fn Client::health(self : Client) -> HealthStatus raise ApiError {
  parse_as(self.do_request(Get, "/system/health", [], None))
}

///|
async fn Client::system_status(self : Client) -> SystemStatus raise ApiError {
  parse_as(self.do_request(Get, "/system/status", [], None))
}

// -----------------------------------------------------------------------------
// Memory CRUD
// -----------------------------------------------------------------------------

///|
async fn Client::create_memory(
  self : Client,
  content~ : String,
  user_id? : String,
  agent_id? : String,
  metadata? : Json,
  infer? : Bool,
) -> Array[Memory] raise ApiError {
  let body = json_object([
    ("content", Some(content.to_json())),
    ("user_id", user_id.map(fn(s) { s.to_json() })),
    ("agent_id", agent_id.map(fn(s) { s.to_json() })),
    ("metadata", metadata),
    ("infer", infer.map(fn(b) { b.to_json() })),
  ])
  parse_as(self.do_request(Post, "/memories", [], Some(body)))
}

///|
async fn Client::list_memories(
  self : Client,
  user_id? : String,
  agent_id? : String,
  limit? : Int,
  offset? : Int,
) -> MemoryList raise ApiError {
  let query : Array[(String, String)] = []
  if user_id is Some(v) {
    query.push(("user_id", v))
  }
  if agent_id is Some(v) {
    query.push(("agent_id", v))
  }
  if limit is Some(v) {
    query.push(("limit", v.to_string()))
  }
  if offset is Some(v) {
    query.push(("offset", v.to_string()))
  }
  parse_as(self.do_request(Get, "/memories", query, None))
}

///|
async fn Client::search_memories(
  self : Client,
  query~ : String,
  user_id? : String,
  agent_id? : String,
  limit? : Int,
) -> SearchResponse raise ApiError {
  let body = json_object([
    ("query", Some(query.to_json())),
    ("user_id", user_id.map(fn(s) { s.to_json() })),
    ("agent_id", agent_id.map(fn(s) { s.to_json() })),
    ("limit", limit.map(fn(n) { n.to_json() })),
  ])
  parse_as(self.do_request(Post, "/memories/search", [], Some(body)))
}

///|
async fn Client::update_memory(
  self : Client,
  memory_id~ : String,
  content? : String,
  metadata? : Json,
) -> Memory raise ApiError {
  let body = json_object([
    ("content", content.map(fn(s) { s.to_json() })),
    ("metadata", metadata),
  ])
  parse_as(self.do_request(Put, "/memories/\{memory_id}", [], Some(body)))
}

///|
async fn Client::delete_memory(
  self : Client,
  memory_id~ : String,
) -> DeletedMemory raise ApiError {
  parse_as(self.do_request(Delete, "/memories/\{memory_id}", [], None))
}
