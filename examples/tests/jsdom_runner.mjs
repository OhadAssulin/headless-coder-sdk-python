#!/usr/bin/env node
import { JSDOM } from 'jsdom';

const chunks = [];
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => chunks.push(chunk));
process.stdin.on('end', async () => {
  try {
    const payload = JSON.parse(chunks.join(''));
    const options = payload.options || {};
    const dom = new JSDOM(payload.html, {
      runScripts: options.runScripts ?? 'dangerously',
      pretendToBeVisual: options.pretendToBeVisual ?? true,
    });
    const window = dom.window;
    const document = window.document;

    const script = payload.script || '';
    const runner = new Function('window', 'document', script);
    const result = await runner(window, document);

    process.stdout.write(JSON.stringify({ ok: true, result }));
  } catch (error) {
    process.stdout.write(
      JSON.stringify({ ok: false, error: { message: error?.message ?? String(error), stack: error?.stack } }),
    );
    process.exitCode = 1;
  }
});
