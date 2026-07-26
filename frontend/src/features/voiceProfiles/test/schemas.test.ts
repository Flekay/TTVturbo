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
  recommended_duration_seconds: 4.5,
  notes: "Ruhig sprechen.",
};

const validReference = {
  script_id: "s1",
  recording_filename: "abc.wav",
  status: "ACCEPTED",
  created_at: "2026-01-01T00:00:00+00:00",
};

const validProgress = {
  total: 10,
  accepted: 1,
  review: 0,
  rejected: 0,
  missing: 9,
  percent: 10,
  clone_ready: false,
  pack_complete: false,
};

const validProfile = {
  id: "p1",
  name: "Meine Stimme",
  locale: "de-DE",
  created_at: "2026-01-01T00:00:00+00:00",
  archived: false,
  references: [validReference],
  progress: validProgress,
};

describe("voiceScriptPackSchema", () => {
  it("parses a valid script response", () => {
    const parsed = voiceScriptPackSchema.parse({
      scripts: [validScript],
      total: 1,
      locale: "de-DE",
    });
    expect(parsed.scripts[0].id).toBe("s1");
    expect(parsed.scripts[0].text).toBe("Hallo Welt.");
  });

  it("tolerates additional fields on scripts", () => {
    const parsed = voiceScriptPackSchema.parse({
      scripts: [{ ...validScript, future_field: "x" }],
    });
    expect(parsed.scripts[0].id).toBe("s1");
  });

  it("rejects a script missing a required field", () => {
    expect(() =>
      voiceScriptPackSchema.parse({
        scripts: [{ ...validScript, id: undefined }],
      }),
    ).toThrow();
  });
});

describe("voiceProfileSchema", () => {
  it("parses a valid profile", () => {
    const parsed = voiceProfileSchema.parse(validProfile);
    expect(parsed.id).toBe("p1");
    expect(parsed.references[0].status).toBe("ACCEPTED");
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
      references: [{ ...validReference, status: "PENDING_REVIEW" }],
    });
    expect(parsed.references[0].status).toBe("PENDING_REVIEW");
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
