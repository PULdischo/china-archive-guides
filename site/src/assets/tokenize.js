// Normalization and tokenization shared with the pipeline (pipeline/src/cah/search.py), which
// builds the search index (pipeline/src/cah/csindex.py). Must match the Python side exactly.
const HAN = "\\u3400-\\u4dbf\\u4e00-\\u9fff\\uf900-\\ufaff";
const HAN_CHAR = new RegExp(`[${HAN}]`, "u");
const RUNS = new RegExp(`[${HAN}]+|[a-z0-9]+`, "gu");
const SPACE_BETWEEN_HAN = new RegExp(`(?<=[${HAN}])\\s+(?=[${HAN}])`, "gu");

export function makeNormalize(t2s) {
  return (text) =>
    Array.from(text || "", (c) => t2s[c] ?? c).join("").toLowerCase().replace(SPACE_BETWEEN_HAN, "");
}

export function tokens(normalized) {
  const out = [];
  for (const run of normalized.match(RUNS) || []) {
    if (HAN_CHAR.test(run)) {
      const chars = Array.from(run);
      out.push(...chars);
      for (let i = 0; i < chars.length - 1; i++) out.push(chars[i] + chars[i + 1]);
    } else out.push(run);
  }
  return out;
}

// Han token -> ASCII key, the form tokens are stored under in the index.
export const encode = (t) => (HAN_CHAR.test(t) ? "h" + Array.from(t, (c) => c.codePointAt(0)).join("x") : t);

// Query terms: Han runs as bigrams (a single character stays a unigram), other words as-is.
export function queryTerms(normalized) {
  const out = [];
  for (const run of normalized.match(RUNS) || []) {
    const chars = Array.from(run);
    if (HAN_CHAR.test(run) && chars.length > 1) {
      for (let i = 0; i < chars.length - 1; i++) out.push(chars[i] + chars[i + 1]);
    } else out.push(run);
  }
  return [...new Set(out)];
}
