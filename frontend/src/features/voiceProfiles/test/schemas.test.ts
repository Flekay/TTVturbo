import { describe, expect, it } from "vitest";
import {
  apiErrorSchema,
  voiceProfileListResponseSchema,
  voiceProfileSchema,
  voiceScriptPackSchema,
} from "../schemas";

const validScript = {
  id: "s1",
  order: 1,
  style: "neutral",
  category: "greeting",
  text: "Hallo Welt.",
  recommended_duration_seconds: { min: 3, max: 5 },
  tags: ["greeting"],
  recording_notes: "Ruhig sprechen.",
};

const validReference = {
  script_id: "s1",
  script_text: "Hallo Welt.",
  category: "greeting",
  style: "neutral",
  recording_filename: "abc.wav",
  recording_sha256: "deadbeef",
  quality: { voice_clone_reference: { quality: "GOOD" } },
  quality_class: "GOOD",
  status: "ACCEPTED",
  review_accepted: false,
  attached_at: "2026-01-01T00:00:00+00:00",
  updated_at: "2026-01-01T00:00:00+00:00",
};

const validProgress = {
  total: 10,
  accepted: 1,
  review: 0,
  rejected: 0,
  missing: 9,
  recorded: 1,
  percentage: 10,
  clone_ready: false,
  pack_complete: false,
};

const validProfile = {
  id: "p1",
  name: "Meine Stimme",
  locale: "de-DE",
  created_at: "2026-01-01T00:00:00+00:00",
  references: { s1: validReference },
  progress: validProgress,
};

describe("voiceScriptPackSchema", () => {
  it("parses a valid script response", () => {
    const parsed = voiceScriptPackSchema.parse({
      pack: { pack_id: "pack1", locale: "de-DE", prompt_count: 1, title: "Test" },
      prompts: [validScript],
    });
    expect(parsed.prompts[0].id).toBe("s1");
    expect(parsed.prompts[0].text).toBe("Hallo Welt.");
  });

  it("tolerates additional fields on scripts", () => {
    const parsed = voiceScriptPackSchema.parse({
      pack: { pack_id: "pack1", locale: "de-DE", prompt_count: 1 },
      prompts: [{ ...validScript, future_field: "x" }],
    });
    expect(parsed.prompts[0].id).toBe("s1");
  });

  it("rejects a script missing a required field", () => {
    expect(() =>
      voiceScriptPackSchema.parse({
        pack: { pack_id: "pack1", locale: "de-DE", prompt_count: 1 },
        prompts: [{ ...validScript, id: undefined }],
      }),
    ).toThrow();
  });
});

describe("voiceProfileSchema", () => {
  it("parses a valid profile", () => {
    const parsed = voiceProfileSchema.parse(validProfile);
    expect(parsed.id).toBe("p1");
    expect(parsed.references.s1.status).toBe("ACCEPTED");
  });

  it("tolerates additional fields on the profile", () => {
    const parsed = voiceProfileSchema.parse({ ...validProfile, extra: 1 });
    expect(parsed.id).toBe("p1");
  });

  it("rejects a profile missing a required field", () => {
    expect(() => voiceProfileSchema.parse({ ...validProfile, id: undefined })).toThrow();
  });

  it("accepts an unknown reference status string", () => {
    const parsed = voiceProfileSchema.parse({
      ...validProfile,
      references: { s1: { ...validReference, status: "PENDING_REVIEW" } },
    });
    expect(parsed.references.s1.status).toBe("PENDING_REVIEW");
  });
});

describe("voiceProfileListResponseSchema", () => {
  it("parses a list of profiles", () => {
    const parsed = voiceProfileListResponseSchema.parse({ profiles: [validProfile] });
    expect(parsed.profiles).toHaveLength(1);
  });

  it("rejects a missing profiles field", () => {
    expect(() => voiceProfileListResponseSchema.parse({})).toThrow();
  });
});

describe("apiErrorSchema", () => {
  it("parses a string detail", () => {
    const parsed = apiErrorSchema.parse({ detail: "boom" });
    expect(parsed.detail).toBe("boom");
  });

  it("parses an array detail", () => {
    const parsed = apiErrorSchema.parse({ detail: ["boom", "x"] });
    expect(parsed.detail).toEqual(["boom", "x"]);
  });
});
