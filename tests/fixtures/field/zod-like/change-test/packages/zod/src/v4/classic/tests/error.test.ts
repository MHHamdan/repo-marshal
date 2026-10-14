import { expect, test } from "vitest";
import { checkUnion } from "../../core/schemas.js";

test("counts options", () => {
  expect(checkUnion(["a"])).toBe(1);
});

test("counts two options", () => {
  expect(checkUnion(["a", "b"])).toBe(2);
});
