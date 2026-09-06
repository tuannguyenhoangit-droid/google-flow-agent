# Capturing a Flow batchexecute payload

`agent/services/flow_batch.py` only knows the RPC shapes that were captured off a
real UI action. Adding one — video upscale, reference-to-video, start+end-frame
chaining, a base-image edit — starts by watching the browser do it, because
guessing at Google's positional payloads does not work. Thirty generations were
spent proving that a reference image in the wrong slot is *accepted* and then
silently ignored.

The recorder is deliberately **not** in the shipped extension: it writes live
request bodies to disk, so it goes in for one session and comes straight back out.

## What already exists

| step | rpcid | notes |
|---|---|---|
| generate image | `ogiZ0b` | signed CDN url comes back inline |
| generate video | `eb1hJf` | returns an operation id |
| poll operation | `jwpduf` | status `CAE` means finished |
| operation → media id | `Zzl0ze` | `projects/<id>`; the listing is ~17 MB |
| media id → urls | `as29s` | signed `/video/` + poster `/image/` |
| upload an image | `maseQ` | base64 in the payload, captcha like a generate |

Missing, and each blocked behind a capture: **video upscale**, **r2v**,
**start+end-frame chaining**, and the **base-image** variant of the image edit.

## Recording one

1. Add to `extension/background.js`, temporarily:

```js
const NETLOG_HOSTS = ['https://flow.google.com/_/*'];
const pending = new Map();

chrome.webRequest.onBeforeRequest.addListener((d) => {
  const body = d.requestBody?.raw?.length
    ? new TextDecoder().decode(new Uint8Array(d.requestBody.raw[0].bytes))
    : null;
  pending.set(d.requestId, { ts: new Date().toISOString(), url: d.url, body });
}, { urls: NETLOG_HOSTS }, ['requestBody']);

chrome.webRequest.onCompleted.addListener((d) => {
  const rec = pending.get(d.requestId);
  if (!rec) return;
  pending.delete(d.requestId);
  fetch('http://127.0.0.1:8100/api/ext/netlog', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ ...rec, statusCode: d.statusCode }),
  }).catch(() => {});
}, { urls: NETLOG_HOSTS });
```

   Headers are omitted on purpose — the payload shape is what is wanted, and the
   credentials are what caused the mess. Add an `onBeforeSendHeaders` listener
   only if a header itself is the open question, and delete the log afterwards.

2. Add a matching throwaway route to `agent/main.py` that appends the posts to a
   file under the scratch directory.
3. Reload the extension, perform **one** action in Flow, then reload again with
   the listener removed.
4. Read the `f.req` out of the capture: it is
   `[[[rpcid, "<inner payload as JSON string>", null, "generic"]]]`. Diff the
   inner payload against the closest builder in `flow_batch.py` to find which
   slot changed.
5. Delete the capture file. It is evidence, not a fixture — put what you learned
   into a builder and a test instead.

## Two things worth knowing before you diff

**Accepted ≠ used.** A wrong arrangement inside a slot comes back 200 and is
then ignored. Prove a reference image with a prompt that never names its
subject; prove an aspect ratio by reading the JPEG header, not by trusting the
field name.

**Slots do not share encodings.** Image aspect is 1 square / 2 portrait /
3 landscape / 4 is 3:4 / 5 is 4:3. Video aspect is 1 portrait / 2 landscape, in
its own slot. Conflating them renders the wrong shape silently.
