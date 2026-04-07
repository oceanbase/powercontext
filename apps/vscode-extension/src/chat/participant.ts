/**
 * Chat participant @powermem: when seamless mode is off, supports remember/save/search
 * and auto-summarize + auto-retrieve. When seamless mode is on, memory is automatic
 * via linked AI (MCP) and file auto-capture; this handler only shows a short redirect.
 */

import * as vscode from 'vscode';
import { addMemory, searchMemories } from '../api/client';
import type { SearchResultItem } from '../api/types';

const MAX_CHAT_MEMORY_CHARS = 12000;
const SUMMARY_PROMPT =
  'Summarize the following conversation into concise bullet points for long-term memory. ' +
  'Keep only important facts, decisions, and context. Output only the summary, same language as the conversation.';

function isSaveIntent(prompt: string, command?: string): boolean {
  const t = prompt.trim().toLowerCase();
  if (command === 'remember' || command === 'save' || command === '\u4fdd\u5b58') return true;
  if (/^(remember|save)(\s|$)/i.test(t)) return true;
  if (/^[\u8bb0\u4f4f\u4fdd\u5b58](\s|$)/.test(t)) return true;
  if (/^(\u628a)?(\u4e0a\u9762)?(\u8fd9\u6bb5)?(\u5bf9\u8bdd)?(\u8bb0\u4e0b\u6765|\u5b58\u5230?\u8bb0\u5fc6|\u4fdd\u5b58\u5230?powermem)/.test(t)) return true;
  return false;
}

function isSearchIntent(prompt: string, command?: string): boolean {
  const t = prompt.trim().toLowerCase();
  if (command === 'search' || command === '\u641c\u7d22') return true; // \u641c\u7d22 = search (localized)
  if (/^(search|query)\s/i.test(t)) return true;
  if (/^[\u641c\u7d22\u67e5\u8be2]\s/.test(t)) return true;
  return false;
}

function responseTurnToText(turn: vscode.ChatResponseTurn): string {
  const parts: string[] = [];
  for (const part of turn.response) {
    if (part instanceof vscode.ChatResponseMarkdownPart) {
      const v = part.value;
      parts.push(typeof v === 'string' ? v : (v as { value?: string }).value ?? '');
    }
  }
  return parts.join('\n').trim();
}

function historyToText(history: ReadonlyArray<vscode.ChatRequestTurn | vscode.ChatResponseTurn>): string {
  const lines: string[] = [];
  for (const turn of history) {
    if (turn instanceof vscode.ChatRequestTurn) {
      lines.push(`[User] ${turn.prompt}`);
    } else {
      const text = responseTurnToText(turn);
      if (text) lines.push(`[Assistant] ${text}`);
    }
  }
  return lines.join('\n\n');
}

function formatMemoriesForPrompt(results: SearchResultItem[]): string {
  if (results.length === 0) return '';
  return results.map((r, i) => `[${i + 1}] ${(r.content ?? '').trim().slice(0, 500)}`).join('\n\n');
}

async function summarizeWithModel(
  model: vscode.LanguageModelChat,
  conversationText: string,
  token: vscode.CancellationToken
): Promise<string> {
  const truncated =
    conversationText.length > MAX_CHAT_MEMORY_CHARS
      ? conversationText.slice(0, MAX_CHAT_MEMORY_CHARS) + '\n…'
      : conversationText;
  const messages = [
    vscode.LanguageModelChatMessage.User(SUMMARY_PROMPT + '\n\n---\n\n' + truncated),
  ];
  const response = await model.sendRequest(messages, {}, token);
  let out = '';
  for await (const chunk of response.text) {
    out += chunk;
  }
  return out.trim();
}

const SEAMLESS_REDIRECT =
  'PowerMem is in **seamless mode**: memory is automatic. Use your linked AI (Cursor, Claude Code, Codex) for chat—they already have retrieval via MCP. File content is added to memory on save when auto-capture is on (default in seamless mode). Disable *Seamless mode* in PowerMem settings to use @powermem commands (remember / save / search).';

export function registerChatParticipant(
  context: vscode.ExtensionContext,
  getBackendUrl: () => string,
  getApiKey: () => string | undefined,
  getUserId: () => string,
  getEnabled: () => boolean,
  getSeamlessMode: () => boolean,
  getChatAutoSummarizeTurns: () => number,
  getChatAutoRetrieve: () => boolean
): void {
  if (typeof vscode.chat?.createChatParticipant !== 'function') return;

  const handler: vscode.ChatRequestHandler = async (
    request: vscode.ChatRequest,
    chatContext: vscode.ChatContext,
    stream: vscode.ChatResponseStream,
    token: vscode.CancellationToken
  ): Promise<vscode.ChatResult | void> => {
    if (getSeamlessMode()) {
      stream.markdown(SEAMLESS_REDIRECT);
      return;
    }
    const enabled = getEnabled();
    const backendUrl = getBackendUrl();
    if (!enabled || !backendUrl) {
      stream.markdown('PowerMem is disabled or not configured. Enable the extension and set Backend URL in settings.');
      return;
    }

    const apiKey = getApiKey();
    const userId = getUserId();
    const prompt = request.prompt.trim();
    const command = request.command;
    const autoSummarizeTurns = getChatAutoSummarizeTurns();
    const autoRetrieve = getChatAutoRetrieve();

    // ——— Explicit save ———
    if (isSaveIntent(prompt, command)) {
      const historyText = historyToText(chatContext.history);
      let toSave = historyText || prompt || '[Empty conversation]';
      if (!historyText && prompt) {
        const stripped = prompt.replace(/^(\u8bb0\u4f4f|\u4fdd\u5b58|remember|save)\s*[:\uFF1A]\s*/i, '').trim();
        if (stripped) toSave = stripped;
      }
      const content =
        toSave.length > MAX_CHAT_MEMORY_CHARS ? toSave.slice(0, MAX_CHAT_MEMORY_CHARS) + '\n…' : toSave;
      try {
        await addMemory(
          backendUrl,
          {
            content,
            user_id: userId || undefined,
            metadata: { source: 'vscode-chat', type: 'chat-history' },
          },
          apiKey
        );
        stream.markdown('Saved to PowerMem.');
      } catch (e) {
        stream.markdown(`Save failed: ${e}`);
      }
      return;
    }

    // ——— Explicit search ———
    if (isSearchIntent(prompt, command)) {
      const query = prompt.replace(/^(\u641c\u7d22|\u67e5\u8be2|search|query)\s*/gi, '').trim() || prompt;
      if (!query) {
        stream.markdown('Enter a search query, e.g. `/search login flow`');
        return;
      }
      stream.progress('Searching…');
      try {
        const data = await searchMemories(
          backendUrl,
          { query, user_id: userId || undefined, limit: 8 },
          apiKey
        );
        const results = data?.results ?? [];
        if (results.length === 0) {
          stream.markdown('No related memories found.');
          return;
        }
        let out = '**PowerMem search results**\n\n';
        for (const r of results) {
          const score = r.score != null ? ` (relevance: ${r.score})` : '';
          out += `- ${(r.content ?? '').slice(0, 300)}${(r.content?.length ?? 0) > 300 ? '…' : ''}${score}\n\n`;
        }
        stream.markdown(out);
      } catch (e) {
        stream.markdown(`Search failed: ${e}`);
      }
      return;
    }

    // ——— General question: auto-summarize (background) + auto-retrieve + answer with LLM ———
    const history = chatContext.history;
    const model = request.model;
    const canUseModel =
      model && (context.languageModelAccessInformation?.canSendRequest?.(model) === true);

    // 1) Auto-summarize every N turns (fire-and-forget)
    if (autoSummarizeTurns >= 2 && history.length >= autoSummarizeTurns - 1 && canUseModel) {
      const start = Math.max(0, history.length - (autoSummarizeTurns - 1));
      const slice = history.slice(start);
      const conversationText = historyToText(slice) + '\n\n[User] ' + prompt;
      summarizeWithModel(model, conversationText, token)
        .then((summary) => {
          if (!summary || token.isCancellationRequested) return;
          return addMemory(
            backendUrl,
            {
              content: summary,
              user_id: userId || undefined,
              metadata: { source: 'vscode-chat', type: 'chat-summary' },
            },
            apiKey
          );
        })
        .catch(() => {});
    }

    // 2) Auto-retrieve relevant memories
    let memoriesText = '';
    if (autoRetrieve && prompt) {
      try {
        const data = await searchMemories(
          backendUrl,
          { query: prompt, user_id: userId || undefined, limit: 6 },
          apiKey
        );
        const results = data?.results ?? [];
        memoriesText = formatMemoriesForPrompt(results);
      } catch {
        // ignore
      }
    }

    // 3) Answer with LLM + memories, or show memories only
    if (canUseModel && model) {
      const systemContent =
        memoriesText.length > 0
          ? `Relevant memories (use when helpful):\n\n${memoriesText}\n\nAnswer the user's question below, using these memories when relevant.`
          : 'Answer the user\'s question concisely.';
      const messages = [
        vscode.LanguageModelChatMessage.User(systemContent),
        vscode.LanguageModelChatMessage.User(prompt),
      ];
      try {
        const response = await model.sendRequest(messages, {}, token);
        for await (const chunk of response.text) {
          if (token.isCancellationRequested) break;
          stream.markdown(chunk);
        }
      } catch (e) {
        if (memoriesText) {
          stream.markdown('**Relevant memories**\n\n' + memoriesText + '\n\n---\n\n*Model request failed: ' + String(e) + '*');
        } else {
          stream.markdown('Model request failed: ' + String(e));
        }
      }
      return;
    }

    // No model: show memories if any, else short help
    if (memoriesText) {
      stream.markdown('**Relevant memories**\n\n' + memoriesText + '\n\n---\n\nSelect a chat model above to get answers using these memories.');
    } else {
      stream.markdown(
        '**PowerMem** auto memory and retrieval are on. After you select a chat model:\n' +
          '- Every N turns are summarized and saved to memory automatically.\n' +
          '- Each answer uses retrieved memories when relevant.\n\n' +
          'You can also use **remember** / **/save** to save, **/search &lt;query&gt;** to search.'
      );
    }
  };

  const participant = vscode.chat.createChatParticipant('powermem-vscode.powermem', handler);
  participant.followupProvider = {
    provideFollowups(_result: vscode.ChatResult, _ctx: vscode.ChatContext, _token: vscode.CancellationToken) {
      return [
        { prompt: 'remember', label: 'Save this conversation to PowerMem' },
        { prompt: 'search recent project notes', label: 'Search PowerMem' },
      ];
    },
  };
  context.subscriptions.push(participant);
}
