---
name: youtube-transcript-to-feishu
description: >-
  Turn a YouTube link into a bilingual (Chinese + English, sentence-by-sentence)
  Feishu doc and add it to the user's "优质信息" wiki index. Use this whenever the
  user shares a YouTube URL and asks for a transcript, 逐字稿, 转写, subtitles,
  summary, or wants it整理成飞书文档 / 加到优质信息. If the detected link is a web
  article instead of a video, keep using this skill but switch all user-facing
  wording from transcript/逐字稿 to bilingual translation/双语翻译稿 while keeping
  the same bilingual paired output. Do not use for non-YouTube videos or local
  uploaded audio/video files.
---

# YouTube Transcript / Article Translation → Feishu Doc → 优质信息 Index

This skill automates a three-stage pipeline the user runs repeatedly:

1. **Detect and extract** the source content from the provided link.
2. **Organize** it into a Feishu doc: title + one-line summary + cleaned,
   **bilingual paired body where each Chinese sentence is immediately followed by
   its corresponding English original** (一句中文一句英文).
3. **Register** that doc in the user's "优质信息" wiki page by appending a new
   numbered entry, keeping the existing list order.

If the link is a **video**, the body is a bilingual **逐字稿**.
If the link is an **article**, the body is a bilingual **翻译稿**.
The output shape stays parallel in both cases; only the user-facing wording
changes so the final document matches the source type.

The point is consistency: every video the user processes should end up looking
like the entries already in 优质信息, so the index stays uniform and browsable.

## The 优质信息 index

Default target: `https://bytedance.larkoffice.com/wiki/Y71mwA20mis695keSdDcLe8inMe`
(unless the user gives a different one). It's a wiki page with a short intro note
followed by a **numbered list** of processed sources. Existing entries look like:

```
1. 《标题》 https://some-link
2. 《标题》 https://some-link
3. 《Lenny's Podcast 访谈逐字稿…》 [飞书链接](https://bytedance.larkoffice.com/docx/xxx)
```

Your new entry must match this style (see "Step 4").

## Prerequisites

This skill drives the **lark-doc** skill for all Feishu operations. Read its
SKILL.md and the relevant references before any create/fetch/update — Feishu doc
editing has strict safety rules (edit-mode confirmation, resource-block fidelity,
batch limits). Do not hand-roll Feishu API calls.

The transcript extractor lives at `scripts/fetch_transcript.py` and needs only
Python 3 (stdlib) plus outbound network. It does **not** require a browser or any
listening process.

---

## Step 1 — Detect source type and extract content

Start by deciding whether the user provided a **video link** or an **article link**.
This decision controls both the extraction method and all user-facing wording in
later steps.

### Article detection logic

Treat the link as an **article** when one or more of these signals are present:

- The URL is a normal web page rather than a canonical YouTube watch / shorts /
  live / youtu.be link.
- The fetched page is dominated by prose paragraphs, headings, and article body
  text rather than media metadata or caption tracks.
- The user explicitly asks for 翻译, 翻译稿, 双语整理, 文章整理, or says the link is
  一篇文章 / 原文 / blog / post / essay.
- The page has a clear article title and contiguous body text, but no usable
  transcript / subtitle structure.

Treat the link as a **video** when one or more of these signals are present:

- The URL is a YouTube watch / shorts / live / youtu.be link.
- The user asks for 逐字稿, transcript, 转写, captions, subtitles, or 视频整理.
- The extracted source is caption-like text tied to a media asset rather than a
  normal article body.

If signals conflict, prefer the **actual source structure over the user's loose
wording**. For example, if the user says “整理这个视频” but the provided link opens
to a normal article page, treat it as an article and switch all output wording to
“双语翻译稿”. Conversely, if the user casually says “翻译一下这个链接” but the link
is clearly a YouTube video, keep the output as a bilingual 逐字稿.

When the source type is still ambiguous after inspection, state the ambiguity
briefly to the user and choose the format that matches the fetched content best.
Do not force article wording onto a transcript, and do not call an article body
a 逐字稿.

### Extraction path

For **video links**, run the bundled transcript script. It tries several
extractors in order and returns clean text even when this environment's IP is
blocked by YouTube's bot check (which it usually is — plain `yt-dlp` and
`youtube-transcript-api` fail here, so don't reach for them directly).

```bash
python3 <skill-path>/scripts/fetch_transcript.py "<youtube url>" --out "$(pwd)/transcript_<video_id>.json"
```

Call the script by its absolute path and write the output into your **current
working directory**, not the skill directory — the skill folder is shared and
read-only-ish, and if several runs all write `transcript.json` into it they clobber
each other. Give the `--out` file a name that includes the video id (or any unique
token) so parallel work never collides.

Always pass `--out` so the full transcript lands in a file (stdout is truncated
to keep your context clean). Read the JSON file to get:

- `title`, `author`, `duration` — for the doc heading and the index entry
- `publish_date` — the video's publish date, already normalized to `YYYY年MM月DD日`
  (may be `null` if it couldn't be determined). This date is **required** in both
  the doc callout and the index entry (see Steps 3 & 4).
- `transcript` — the raw text to clean and translate
- `source` — which extractor worked (informational)

If `ok` is `false`, the video has no accessible captions (or all extractors are
down). Tell the user plainly which video failed and why; don't fabricate a
transcript. Suggest they check the link is public and has captions.

If `publish_date` comes back `null`, try once to recover it (e.g. read the
publish date shown on the video page) and otherwise write `发布日期未知` rather
than inventing a date — never fabricate a date.

For **article links**, do not use the transcript extractor. Instead, fetch the
article body with the normal web/content-reading path, keep only the meaningful
title + main body text, and discard navigation, footer, related links, author-bio
boilerplate, and other page chrome before translation. **Also capture the
article's publish date** from its byline / `<time>` / metadata (`article:published_time`,
`datePublished`, or the visible dateline) and normalize it to `YYYY年MM月DD日`;
if none is present, use `发布日期未知` rather than guessing.

**Why a script and not inline code for videos:** the extraction logic (id
parsing, the kome.ai → yt-dlp → transcript-api fallback chain, VTT cleanup) is
fiddly and identical every time. Bundling it means every run is fast and reliable
instead of re-derived from scratch.

---

## Step 2 — Clean and produce the bilingual body

Transform the source content into a readable **bilingual** body: each Chinese
sentence is paired with the English sentence it translates, one right after the
other (一句中文一句英文).

- For **video links**, this is a cleaned bilingual **逐字稿**: preserve the
  speaker's wording while removing rolling-caption noise.
- For **article links**, this is a bilingual **翻译稿**: preserve the article's
  meaning, paragraph flow, and sentence correspondence, but refer to it as 翻译稿
  rather than 逐字稿 / 转写 in all user-facing wording.

Raw captions are messy: no punctuation, broken mid-sentence, rolling-caption
duplicates, filler ("um", "you know", "like"). Raw article pages may contain
navigation, boilerplate, share text, or unrelated chrome. Your job is to make
it read like a clean, sentence-aligned bilingual source document.

**The bilingual pairing is the heart of this skill.** The user reads the Chinese
to follow along quickly and glances at the English to check the source's exact
wording, so the two must actually correspond line for line — a Chinese line whose
English partner says something else defeats the purpose.

- **Pair sentence by sentence.** Segment the source into natural sentences, then
  for each one output the Chinese translation first, then the original English
  directly beneath it. Keep the pairing tight: one Chinese line ↔ one English
  line covering the same content. If a raw caption sentence is very long, you may
  split it into two aligned pairs, but never let a Chinese line drift away from
  the English it sits next to.
- **Format each pair as two lines in one paragraph** so they stay visually
  grouped: the Chinese line, a soft line break, then the English line set off in
  a lighter weight so the eye can tell them apart. Use this exact shape:

  ```
  <p>{中文译文}<br/><em><span text-color="gray">{original English}</span></em></p>
  ```

  One `<p>` per sentence pair. The Chinese is the primary line; the English sits
  under it in gray italic as the reference original. Use `<em>` for italic and
  `<span text-color="gray">` to lighten it — these are the tags lark-doc's XML
  format supports (`<i>` is not valid there).
- **Clean both sides, don't summarize.** Whether the source is a 逐字稿 or a 翻译稿,
  preserve the original content and meaning on *both* the English and the Chinese
  side. For videos, remove only filler and rolling-caption duplication from the
  English before translating. For articles, remove only page chrome and clearly
  non-body noise before translating. Do not drop substantive points or compress
  arguments into bullets. The cleaned English is what you translate, so the two
  sides stay in lockstep.
- **Translation quality — this is what "把内容翻译得更好一点" means.** The Chinese
  line is the line the user actually reads; the English is only a reference they
  glance at. So the Chinese must stand on its own as writing a native speaker
  would produce — faithful to the meaning, but never sounding like it was
  machine-translated. Hold every Chinese line to these standards:
  - **翻译意思,不要翻译词序 (translate meaning, not word order).** English
    sentence structure (long pre-modifiers, "which/that" relative clauses,
    passive voice, "it is ... that", nominalizations like "the implementation of")
    must be re-expressed in natural Chinese, not mirrored. Break one long English
    sentence into several short Chinese clauses when that reads better; reorder so
    the topic comes first as Chinese prefers. If a back-translation of your Chinese
    would reproduce the English word-for-word, you have under-translated — rewrite.
  - **Kill translationese (翻译腔).** Actively remove the tells: overused "的...的...的"
    chains, mechanical "作为一个...", "对于...来说" openers on every sentence, stiff
    "被" passives where Chinese would use an active or agentless form, "进行/做出 +
    抽象名词" ("进行优化" → "优化"), and pronoun spam ("它/他们/这个" repeated where
    Chinese would just drop the subject). Prefer verbs over nominalizations and
    concrete phrasing over abstract.
  - **Match register and voice to the source.** A casual podcast/interview should
    read like natural spoken Chinese — rhetorical questions, "其实/说白了/坦白讲/你懂的"
    where the speaker is being colloquial — not like a written report. A formal
    essay should read like polished written Chinese. Preserve the speaker's
    personality, emphasis, hedging ("I think", "kind of", "honestly") and humor
    rather than flattening everything to neutral prose. Keep the first-person
    "我/我们" energy in interviews.
  - **Render idioms and figures of speech by intent, not literally.** Translate
    what an idiom, metaphor, or piece of slang *means* in idiomatic Chinese; only
    keep a literal image when it also works in Chinese. Numbers, units, and dates
    should follow Chinese convention where natural.
  - **Keep terminology consistent across the whole doc.** Decide once how each key
    term, name, and recurring concept is rendered, then use that choice every time
    — don't translate "evaluation/eval" as 评估 in one line and 测评 in the next.
    When you work in segments, carry a running glossary so segment 5 matches
    segment 1.
  - **Mix English terms the way the user does.** Keep proper nouns, product names,
    and well-known English terms (e.g. Claude Code, Anthropic, MCP, API, PRD, token,
    prompt, agent) in their original form inside the Chinese line where that reads
    more naturally than a forced translation — mirror how the user blends English
    terms into Chinese in their own notes. But do translate ordinary words that
    have clean, established Chinese equivalents; don't leave English in out of
    laziness.
  - **Faithful, not free.** All of the above serves readability, never license to
    add, drop, soften, or editorialize. Every claim, hedge, number, and nuance in
    the English must survive in the Chinese. Naturalness and fidelity are both
    required — if they seem to conflict, rephrase until you have both.
- **Restore structure**: group related sentence pairs into topical blocks. For
  long videos, add `## 小标题` section headings (Chinese) where the topic clearly
  shifts — it makes a 1-hour transcript navigable. Section headings are
  Chinese-only; only the transcript sentences are bilingual.
- **Preserve speakers** in interviews/podcasts. If the transcript implies turns
  (host vs. guest), mark them (e.g. **主持人：** / **嘉宾：**) at the start of that
  speaker's first pair when you can infer them confidently; don't invent names.
- **What if the source is already Chinese?** A Chinese-language video has no
  English original to pair with — in that case skip the English line and just
  output clean Chinese sentences, and note in the report that the source was
  already Chinese so no bilingual pairing was possible.

For very long transcripts, work in segments rather than one giant pass, keeping
the sentence pairing consistent across segments, and assemble the full bilingual
text before creating the doc.

**Example of a sentence pair:**
```
<p>我们一开始完全没料到人们会这样使用这个产品。<br/><em><span text-color="gray">We had absolutely no idea people would use the product this way at first.</span></em></p>
<p>所以我们花了好几个月重新思考整个架构。<br/><em><span text-color="gray">So we spent several months rethinking the entire architecture.</span></em></p>
```

---

## Step 3 — Create the transcript Feishu doc

Create a new Feishu doc **in the same wiki space as 优质信息** so all transcripts
live together. Get the wiki space from the index URL: fetch the index first
(`lark-doc docs +fetch`) — the response carries the wiki node, and you can create
the new doc under the same `--wiki-space` (use `lark-cli docs +create` flags
`--wiki-space` / `--wiki-node`, or `--parent-token` with the space's node token).
If you cannot resolve the space, fall back to a normal doc and tell the user where
it landed.

Use XML format (default). Document structure:

For **video links**:

```
<title>{中文标题} 逐字稿</title>
<callout>
  <p>📺 原视频：<a href="{youtube url}">{原视频标题}</a>｜{author}｜时长 {duration}｜发布日期 {YYYY年MM月DD日}</p>
</callout>
<h1>一句话总结</h1>
<p>{one-sentence summary of what the video is about, in Chinese}</p>
<h1>逐字稿（中英对照）</h1>
{cleaned bilingual transcript — one <p> per sentence pair
 (中文<br/><em><span text-color="gray">English</span></em>),
 with ## section headings for long videos}
```

For **article links**:

```
<title>{中文标题} 双语翻译稿</title>
<callout>
  <p>📰 原文：<a href="{article url}">{原文标题}</a>｜发布日期 {YYYY年MM月DD日}</p>
</callout>
<h1>一句话总结</h1>
<p>{one-sentence summary of what the article is about, in Chinese}</p>
<h1>翻译稿（中英对照）</h1>
{cleaned bilingual article translation — one <p> per sentence pair
 (中文<br/><em><span text-color="gray">English</span></em>),
 with ## section headings for long articles}
```

The one-line summary sits at the top so the user can tell at a glance what the
source covers without opening the full body. The summary is Chinese-only;
the bilingual pairing applies to the main body, not the summary.

**Title convention:** use `{中文标题} 逐字稿` for video links, and `{中文标题} 双语翻译稿`
for article links. Keep it concise; this title is what shows up in the wiki tree
and in the index entry.

**Date convention:** the callout must always carry the source's publish date as
`发布日期 {YYYY年MM月DD日}` (e.g. `发布日期 2026年06月21日`) — exactly to the day,
zero-padded month and day. Use the `publish_date` field from the transcript JSON
for videos, or the article's own published date for articles. If the date truly
cannot be determined, write `发布日期未知`; never invent one.

After creation, capture the returned doc URL — you need it for Step 4. Don't
re-fetch to "verify"; a successful create is done.

---

## Step 4 — Append to the 优质信息 index

Add one new numbered entry to the **end** of the existing numbered list,
continuing the numbering. Match entry #3's style exactly: the doc title in
book-title brackets `《》`, followed by a link whose **anchor text is `飞书链接`** —
not the title repeated. Each entry also carries the source's **publish date** in
parentheses `（{YYYY年MM月DD日}）`, placed right after the 《标题》 and before the
link. This keeps every transcript row in the index visually identical
(《title》（日期） 飞书链接), which is the whole point of the uniform index.

**Entry format:**
```
{N}. 《{中文标题} 逐字稿》（{YYYY年MM月DD日}） [飞书链接](https://bytedance.larkoffice.com/docx/xxx)
```

For article links, change only the source-type wording:

```
{N}. 《{中文标题} 双语翻译稿》（{YYYY年MM月DD日}） [飞书链接](https://bytedance.larkoffice.com/docx/xxx)
```

Where `{N}` is the next number after the current last item, and `{YYYY年MM月DD日}`
is the source's publish date (exactly to the day, e.g. `2026年06月21日`; use
`发布日期未知` only if it genuinely can't be found). The link anchor is
literally the two characters `飞书链接`; the actual doc URL goes in the parentheses.

**How to do it safely:**
1. `lark-doc docs +fetch --detail with-ids` the index to get the current list,
   the last item's number, and the block id of the last list item.
2. Append the new list item after the last one (`block_insert_after`), so
   existing entries keep their order and numbering — never rewrite the whole list.
3. The index is shared and confidential (L3). This is an **append to the user's
   own working index**, which is the skill's whole purpose, so original-doc
   editing is expected here — but still confirm edit mode if the user hasn't made
   it clear, per lark-doc's rules.

**Ordering:** "按列表顺序排列" means preserve the existing order and add the new
item at the end. Don't re-sort or renumber prior entries.

---

## End-to-end example

**Input:** user pastes `https://www.youtube.com/watch?v=cdiD-9MMpb0` and says
"整理一下加到优质信息".

**Flow:**
1. `python3 <skill-path>/scripts/fetch_transcript.py "https://www.youtube.com/watch?v=cdiD-9MMpb0" --out "$(pwd)/transcript_cdiD-9MMpb0.json"`
2. Read the JSON file → title, duration, **publish_date**, raw transcript.
3. Clean + build the bilingual transcript (中文 + 对应英文原文, 一句一对) with
   Chinese section headings.
4. Fetch index → resolve wiki space → `lark-doc docs +create` the transcript doc
   in that space with the title/summary/transcript structure, and the callout
   carrying `发布日期 {YYYY年MM月DD日}`.
5. Fetch index with ids → append `4. 《… 逐字稿》（{YYYY年MM月DD日}） [飞书链接](url)`
   after the last item.
6. Report back: the new transcript doc link + confirmation it's entry #4 in 优质信息,
   including the source's publish date.

## Reporting back

When done, give the user: the new transcript doc link, its position in the index
(e.g. "已作为第 4 条加入优质信息"), the source's publish date (年-月-日), and a one-line
note of the video title. If anything was degraded (e.g. transcript doc created
outside the wiki space because the space couldn't be resolved, extraction fell
back to a lower-quality source, or the publish date couldn't be determined), say so.
