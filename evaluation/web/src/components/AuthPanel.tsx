import { useState } from "react";

import type { EvaluationApi } from "../api";

interface AuthPanelProps {
  api: EvaluationApi;
}

export function AuthPanel({ api }: AuthPanelProps) {
  const [value, setValue] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");

  const submit = async () => {
    if (pending || !value.trim()) return;
    setPending(true);
    setMessage("");
    try {
      await api.updateAuth(value.trim());
      setMessage("认证已更新。下一次用量探测和任务执行将使用新认证。");
      setValue("");
    } catch {
      setMessage("更新失败，请检查输入是否为完整的 auth.json 内容。");
    } finally {
      setPending(false);
    }
  };

  return (
    <section className="panel auth-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Codex 认证</p>
          <h2>更换账号凭证</h2>
        </div>
      </div>
      <p className="auth-help">
        在本地终端执行 <code>cat ~/.codex/auth.json</code>，将输出的完整 JSON 粘贴到下方。
        该认证将用于调用 Codex API 和读取账号用量。更新后无需重启服务。
      </p>
      <div className="auth-editor">
        <label htmlFor="codex-auth-json">auth.json 内容</label>
        <textarea
          id="codex-auth-json"
          className="auth-textarea"
          rows={8}
          placeholder='{"auth_mode":"chatgpt","tokens":{...}}'
          value={value}
          onChange={(event) => setValue(event.target.value)}
          spellCheck={false}
        />
        <button
          className="primary-button auth-submit"
          type="button"
          disabled={pending || !value.trim()}
          onClick={submit}
        >
          {pending ? "正在更新…" : "保存认证"}
        </button>
      </div>
      {message && <p className="form-feedback" aria-live="polite">{message}</p>}
    </section>
  );
}
