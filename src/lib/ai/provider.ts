import "server-only";
import Anthropic from "@anthropic-ai/sdk";
import { getEnv } from "@/lib/env";

/**
 * Server-side AI provider abstraction. The research service depends only on
 * this interface, so the model vendor can change without touching it.
 */

export interface CompletionRequest {
  system: string;
  /** User-turn content: retrieved data blocks followed by the question. */
  user: string;
}

export interface CompletionResult {
  text: string;
  model: string;
  inputTokens: number;
  outputTokens: number;
  /** True when the primary model declined and a fallback model answered. */
  servedByFallback: boolean;
}

export class AIProviderError extends Error {
  constructor(
    public readonly kind: "not_configured" | "rate_limited" | "unavailable" | "refused" | "invalid_request" | "truncated",
    message: string,
  ) {
    super(message);
    this.name = "AIProviderError";
  }
}

export interface AIProvider {
  readonly id: string;
  readonly model: string;
  complete(req: CompletionRequest): Promise<CompletionResult>;
}

/**
 * Anthropic Messages API via the official SDK.
 * - Model from AI_MODEL (recommended: claude-opus-5-5).
 * - Adaptive thinking is on by default for current models; depth via effort.
 * - Server-side refusal fallbacks are enabled ("default" routing) so a
 *   safety-classifier decline is retried on a suitable fallback model.
 */
export class AnthropicProvider implements AIProvider {
  readonly id = "anthropic";
  private readonly client: Anthropic;

  constructor(
    apiKey: string,
    readonly model: string,
    private readonly maxTokens: number,
    timeoutMs: number,
  ) {
    this.client = new Anthropic({ apiKey, timeout: timeoutMs, maxRetries: 2 });
  }

  async complete(req: CompletionRequest): Promise<CompletionResult> {
    let response;
    try {
      response = await this.client.beta.messages.create({
        model: this.model,
        max_tokens: this.maxTokens,
        betas: ["server-side-fallback-2026-07-01"],
        fallbacks: "default",
        output_config: { effort: "medium" },
        system: req.system,
        messages: [{ role: "user", content: req.user }],
      });
    } catch (err) {
      if (err instanceof Anthropic.RateLimitError) throw new AIProviderError("rate_limited", "The AI provider is rate limiting requests. Please try again shortly.");
      if (err instanceof Anthropic.AuthenticationError || err instanceof Anthropic.PermissionDeniedError) {
        throw new AIProviderError("not_configured", "The AI provider rejected our credentials.");
      }
      if (err instanceof Anthropic.BadRequestError) throw new AIProviderError("invalid_request", "The AI request was rejected as invalid.");
      if (err instanceof Anthropic.APIError || err instanceof Anthropic.APIConnectionError) {
        throw new AIProviderError("unavailable", "The AI provider is temporarily unavailable.");
      }
      throw new AIProviderError("unavailable", "The AI provider is temporarily unavailable.");
    }

    if (response.stop_reason === "refusal") {
      throw new AIProviderError("refused", "The AI model declined to answer this request.");
    }
    const text = response.content
      .filter((b): b is Anthropic.Beta.BetaTextBlock => b.type === "text")
      .map((b) => b.text)
      .join("\n")
      .trim();
    if (!text) {
      throw new AIProviderError(response.stop_reason === "max_tokens" ? "truncated" : "unavailable", "The AI model returned no answer.");
    }
    const servedByFallback = (response.usage.iterations ?? []).some((e) => e.type === "fallback_message");
    return {
      text,
      model: response.model,
      inputTokens: response.usage.input_tokens,
      outputTokens: response.usage.output_tokens,
      servedByFallback,
    };
  }
}

export function getAIProvider(): AIProvider | null {
  const env = getEnv();
  if (env.AI_PROVIDER === "anthropic" && env.ANTHROPIC_API_KEY && env.AI_MODEL) {
    return new AnthropicProvider(env.ANTHROPIC_API_KEY, env.AI_MODEL, env.AI_MAX_OUTPUT_TOKENS, env.AI_TIMEOUT_MS);
  }
  return null;
}
