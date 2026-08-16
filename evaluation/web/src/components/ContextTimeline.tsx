import { useEffect, useRef, useState } from "react";

import type { EvaluationApi } from "../api";
import type { ContextEvent } from "../types";

interface ContextTimelineProps {
  api: EvaluationApi;
  batchId: string;
  taskId: string;
  attemptId?: string;
}

const PAGE_SIZE = 200;

export function ContextTimeline({ api, batchId, taskId, attemptId }: ContextTimelineProps) {
  const [arm, setArm] = useState<"off" | "on">("on");
  const [events, setEvents] = useState<ContextEvent[] | null>(null);
  const [selected, setSelected] = useState<ContextEvent | null>(null);
  const [error, setError] = useState(false);
  const generation = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    const currentGeneration = ++generation.current;
    setEvents(null);
    setSelected(null);
    setError(false);
    void (async () => {
      try {
        const collected: ContextEvent[] = [];
        let offset = 0;
        while (true) {
          const page = await api.listContextEvents(
            batchId,
            taskId,
            arm,
            {
              limit: PAGE_SIZE,
              offset,
              ...(attemptId === undefined ? {} : { attempt_id: attemptId }),
            },
            controller.signal,
          );
          if (controller.signal.aborted || currentGeneration !== generation.current) return;
          collected.push(...page.items);
          offset += page.items.length;
          if (offset >= page.total) break;
          if (page.items.length === 0) throw new Error("Timeline pagination made no progress");
        }
        setEvents(collected);
        setSelected(collected[0] ?? null);
      } catch {
        if (!controller.signal.aborted && currentGeneration === generation.current) setError(true);
      }
    })();
    return () => {
      controller.abort();
      generation.current += 1;
    };
  }, [api, arm, attemptId, batchId, taskId]);

  return (
    <section className="report-section context-section" aria-labelledby="context-heading">
      <div className="section-heading">
        <div>
          <h2 id="context-heading">完整上下文时间线</h2>
          <p>{taskId} · 按实际观察时间排序</p>
        </div>
        <div className="timeline-tabs" role="group" aria-label="选择实验组时间线">
          <button
            type="button"
            className={arm === "off" ? "timeline-tab timeline-tab--active" : "timeline-tab"}
            aria-pressed={arm === "off"}
            onClick={() => setArm("off")}
          >
            OFF 时间线
          </button>
          <button
            type="button"
            className={arm === "on" ? "timeline-tab timeline-tab--active" : "timeline-tab"}
            aria-pressed={arm === "on"}
            onClick={() => setArm("on")}
          >
            ON 时间线
          </button>
        </div>
      </div>

      {error ? (
        <div className="empty-state">上下文时间线暂时无法加载。</div>
      ) : events === null ? (
        <div className="state-message">正在读取 {arm.toUpperCase()} 时间线…</div>
      ) : events.length === 0 ? (
        <div className="empty-state">当前实验组没有保留上下文事件。</div>
      ) : (
        <div className="timeline-layout">
          <ol className="context-timeline" aria-label={`${arm.toUpperCase()} 上下文事件`}>
            {events.map((event) => {
              const injection = event.event_type === "powercontext_injection";
              const label = injection ? "PowerContext 注入" : event.event_type;
              return (
                <li key={event.sequence}>
                  <button
                    type="button"
                    className={[
                      "context-event",
                      injection ? "context-event--injection" : "",
                      selected?.sequence === event.sequence ? "context-event--selected" : "",
                    ].filter(Boolean).join(" ")}
                    onClick={() => setSelected(event)}
                    aria-label={`#${event.sequence} ${label}`}
                  >
                    <span><strong>#{event.sequence}</strong> {label}</span>
                    <small>{event.actor} · +{event.elapsed_ms} ms</small>
                  </button>
                </li>
              );
            })}
          </ol>
          {selected !== null && <EventDetail event={selected} />}
        </div>
      )}
    </section>
  );
}

function EventDetail({ event }: { event: ContextEvent }) {
  const injection = event.event_type === "powercontext_injection";
  return (
    <article className="event-detail" aria-label="事件详情">
      <header>
        <span>#{event.sequence}</span>
        <h3>{injection ? "PowerContext 注入" : event.event_type}</h3>
        <p>{event.observed_at}</p>
        <p>+{event.elapsed_ms} ms · {event.actor}</p>
      </header>
      {injection ? <InjectionDetail event={event} /> : <GenericEventDetail event={event} />}
      <dl className="event-source">
        <div><dt>来源</dt><dd>{event.source_artifact}</dd></div>
        <div><dt>来源序号</dt><dd>{event.source_sequence}</dd></div>
      </dl>
    </article>
  );
}

function InjectionDetail({ event }: { event: ContextEvent }) {
  const input = event.input ?? {};
  const output = event.output ?? {};
  const hits = Array.isArray(output.hits) ? output.hits : [];
  return (
    <div className="injection-detail">
      {typeof input.query === "string" && <DetailBlock label="检索查询" value={input.query} />}
      {typeof input.scope_id === "string" && <DetailBlock label="Scope" value={input.scope_id} />}
      {typeof input.session_id === "string" && <DetailBlock label="Session" value={input.session_id} />}
      {typeof input.turn_id === "string" && <DetailBlock label="目标 Turn" value={input.turn_id} />}
      {hits.length > 0 && (
        <section>
          <h4>返回上下文</h4>
          <ol className="injection-hits">
            {hits.map((hit, index) => (
              <li key={index}><SafeJson value={hit} /></li>
            ))}
          </ol>
        </section>
      )}
      {typeof output.injected_text === "string" && <DetailBlock label="实际注入文本" value={output.injected_text} />}
    </div>
  );
}

function GenericEventDetail({ event }: { event: ContextEvent }) {
  return (
    <>
      {event.input !== null && (
        <section className="event-json">
          <h4>输入</h4>
          <SafeJson value={event.input} />
        </section>
      )}
      {event.output !== null && (
        <section className="event-json">
          <h4>输出</h4>
          <SafeJson value={event.output} />
        </section>
      )}
    </>
  );
}

function DetailBlock({ label, value }: { label: string; value: string }) {
  return (
    <section className="event-json">
      <h4>{label}</h4>
      <pre>{value}</pre>
    </section>
  );
}

function SafeJson({ value }: { value: unknown }) {
  return <pre>{JSON.stringify(value, null, 2)}</pre>;
}
