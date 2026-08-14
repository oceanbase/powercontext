import { Context } from "@deepseek-ai/cordis";

//#region src/config.d.ts
interface PluginConfig {
  baseUrl?: string;
  authorization?: string;
  scopeId?: string;
  timeoutMs?: number;
  requestTimeoutMs?: number;
  maxBytes?: number;
  capturePrompts?: boolean;
  flushOnCapture?: boolean;
  flushMaxCalls?: number;
}
interface ResolvedConfig {
  baseUrl: string;
  authorization: string | undefined;
  scopeId: string | undefined;
  timeoutMs: number;
  requestTimeoutMs: number;
  maxBytes: number;
  capturePrompts: boolean;
  flushOnCapture: boolean;
  flushMaxCalls: number;
}
//#endregion
//#region src/index.d.ts
declare const name = "powercontext-dsh";
declare const inject: string[];
interface Config extends PluginConfig {}
declare const Config: {
  '~standard': {
    version: 1;
    vendor: string;
    validate(value: unknown): {
      value: ResolvedConfig;
      issues?: undefined;
    } | {
      issues: {
        message: string;
      }[];
      value?: undefined;
    };
  };
};
declare function apply(ctx: Context, config: Config): Promise<void>;
//#endregion
export { Config, apply, inject, name };
