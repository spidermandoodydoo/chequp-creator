#!/usr/bin/env node
// Render a resolved CheqUp storyboard (JSON) to a Meta-ready MP4.
//
//   node render/render.mjs path/to/storyboard.json [--out dir] [--format 9x16|4x5|1x1] [--jobs 4]
//
// Each scene is built in stage.html from the design-system tokens, seeked frame by
// frame in headless Chromium and piped straight into ffmpeg. Full-bleed video b-roll
// (AI clips from mama's GPUs) is composited underneath the transparent overlay in
// the same ffmpeg pass. Scenes render in parallel, then concat + audio mix.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const require = createRequire(import.meta.url);
const { chromium } = await import('playwright').catch(() => require('playwright'));

const FORMATS = {
  '9x16': { vw: 405, vh: 720, dpr: 8 / 3, W: 1080, H: 1920 },
  '4x5': { vw: 432, vh: 540, dpr: 2.5, W: 1080, H: 1350 },
  '1x1': { vw: 432, vh: 432, dpr: 2.5, W: 1080, H: 1080 },
};
const VIDEO = /\.(mp4|mov|webm|mkv)$/i;

function args() {
  const a = process.argv.slice(2), o = { jobs: 4 };
  for (let i = 0; i < a.length; i++) {
    if (a[i] === '--out') o.out = a[++i];
    else if (a[i] === '--format') o.format = a[++i];
    else if (a[i] === '--jobs') o.jobs = +a[++i];
    else if (a[i] === '--keep') o.keep = true;
    else o.board = a[i];
  }
  if (!o.board) { console.error('usage: render.mjs storyboard.json [--out dir] [--format 9x16|4x5|1x1] [--jobs N]'); process.exit(2); }
  return o;
}

function run(cmd, argv, { stdin } = {}) {
  return new Promise((res, rej) => {
    const p = spawn(cmd, argv, { stdio: [stdin ? 'pipe' : 'ignore', 'ignore', 'pipe'] });
    let err = ''; p.stderr.on('data', d => { err += d; if (err.length > 20000) err = err.slice(-10000); });
    p.on('error', rej);
    p.on('close', c => c === 0 ? res() : rej(new Error(`${cmd} exited ${c}\n${err.slice(-3000)}`)));
    if (stdin) stdin(p);
  });
}

// Static server: /x → chequp-creator/x ; /@fs/<abs> → any absolute file (media on mama's disks).
function serve() {
  const types = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.woff2': 'font/woff2' };
  const srv = http.createServer((req, res) => {
    let p = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    const f = p.startsWith('/@fs/') ? p.slice(4).replace(/^\/([A-Za-z]:)/, '$1') : path.join(ROOT, p);
    fs.readFile(f, (e, buf) => {
      if (e) { res.writeHead(404); res.end(); return; }
      res.writeHead(200, { 'content-type': types[path.extname(f).toLowerCase()] || 'application/octet-stream' }); res.end(buf);
    });
  });
  return new Promise(r => srv.listen(0, '127.0.0.1', () => r(srv)));
}

const fsUrl = abs => '/@fs/' + abs.replace(/\\/g, '/').replace(/^\//, '');

async function renderScene(browser, base, board, sc, i, t0, fmt, outDir) {
  const F = FORMATS[fmt], fps = board.fps || 30, n = Math.round(sc.dur * fps);
  const underlay = sc.type === 'media' && sc.src && VIDEO.test(sc.src) && (sc.frame || 'full') === 'full' ? sc.src : null;
  const spec = { ...sc, format: fmt, mode: board.mode, logo_paths: board._logo, t0,
    src: sc.src && !underlay ? fsUrl(sc.src) : sc.src, captions: board.captions, caption_words: board.caption_words, caption_pos: sc.caption_pos, show_captions: sc.show_captions };
  const page = await browser.newPage({ viewport: { width: F.vw, height: F.vh }, deviceScaleFactor: F.dpr });
  page.on('pageerror', e => console.error(`  scene ${i} page error:`, e.message));
  await page.goto(`${base}/render/stage.html`);
  await page.waitForFunction(() => typeof window.__load === 'function');
  await page.evaluate(s => window.__load(s), spec);
  const out = path.join(outDir, `scene_${String(i).padStart(2, '0')}.mp4`);
  const enc = ['-c:v', 'libx264', '-preset', 'medium', '-crf', '14', '-pix_fmt', 'yuv420p', '-r', String(fps), '-an'];
  const pipeIn = ['-f', 'image2pipe', '-c:v', 'png', '-framerate', String(fps), '-i', '-'];
  const argv = underlay
    ? ['-y', '-loglevel', 'error', '-stream_loop', '-1', '-i', underlay, ...pipeIn, '-filter_complex',
      `[0:v]scale=${F.W}:${F.H}:force_original_aspect_ratio=increase,crop=${F.W}:${F.H},setsar=1,fps=${fps},trim=duration=${sc.dur},setpts=PTS-STARTPTS[bg];[1:v]scale=${F.W}:${F.H}[fg];[bg][fg]overlay=format=auto,format=yuv420p`,
      '-frames:v', String(n), ...enc, out]
    : ['-y', '-loglevel', 'error', ...pipeIn, '-vf', `scale=${F.W}:${F.H},format=yuv420p`, '-frames:v', String(n), ...enc, out];
  let poster = null;
  await run('ffmpeg', argv, {
    stdin: async p => {
      try {
        for (let f = 0; f < n; f++) {
          await page.evaluate(ms => window.__seek(ms), (f / fps) * 1000);
          const buf = await page.screenshot({ type: 'png', omitBackground: !!underlay || sc.type === 'media' });
          if (i === 0 && f === Math.min(n - 1, Math.round(fps * 1.6))) poster = buf;
          if (!p.stdin.write(buf)) await new Promise(r => p.stdin.once('drain', r));
        }
      } finally { p.stdin.end(); }
    },
  });
  await page.close();
  return { out, poster };
}

async function mixAudio(board, boardDir, silent, final, dur) {
  const abs = p => p && (path.isAbsolute(p) ? p : path.join(boardDir, p));
  const vo = abs(board.audio?.vo), music = abs(board.audio?.music);
  const inputs = ['-i', silent], parts = [];
  let idx = 1;
  if (vo) { inputs.push('-i', vo); parts.push(`[${idx++}:a]aresample=48000,apad,atrim=0:${dur}[vo]`); }
  if (music) { inputs.push('-stream_loop', '-1', '-i', music); parts.push(`[${idx++}:a]aresample=48000,atrim=0:${dur},volume=${board.audio?.music_gain_db ?? -18}dB,afade=t=out:st=${Math.max(0, dur - 1)}:d=1[mu]`); }
  const LOUD = 'loudnorm=I=-14:TP=-1:LRA=11';
  let graph, map = '[a]';
  if (vo && music) graph = [...parts, '[vo]asplit=2[vo1][vo2]',   // VO keys the duck and is mixed on top
    '[mu][vo1]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[duck]',
    `[vo2][duck]amix=inputs=2:duration=first:normalize=0,${LOUD}[a]`];
  else if (vo) graph = [...parts, `[vo]${LOUD}[a]`];
  else if (music) graph = [...parts, `[mu]${LOUD}[a]`];
  else { inputs.push('-f', 'lavfi', '-t', String(dur), '-i', 'anullsrc=r=48000:cl=stereo'); map = '1:a'; }
  const argv = ['-y', '-loglevel', 'error', ...inputs, ...(graph ? ['-filter_complex', graph.join(';')] : []),
    '-map', '0:v', '-map', map, '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-t', String(dur), '-movflags', '+faststart', final];
  await run('ffmpeg', argv);
}

async function main() {
  const o = args();
  const boardPath = path.resolve(o.board), boardDir = path.dirname(boardPath);
  const board = JSON.parse(fs.readFileSync(boardPath, 'utf8'));
  const fmt = o.format || board.format || '9x16';
  if (!FORMATS[fmt]) throw new Error('unknown format ' + fmt);
  const outDir = path.resolve(o.out || path.join(boardDir, 'out'));
  const work = path.join(outDir, `.work_${board.id}_${fmt}`);
  fs.mkdirSync(work, { recursive: true });
  board._logo = fs.readFileSync(path.join(ROOT, 'brand/design-system/assets/logo-wordmark.svg'), 'utf8').replace(/^[\s\S]*?<svg[^>]*>|<\/svg>\s*$/g, '');
  board.scenes.forEach(s => { if (s.src && !path.isAbsolute(s.src) && !/^https?:/.test(s.src)) s.src = path.join(boardDir, s.src); });

  const srv = await serve();
  const base = `http://127.0.0.1:${srv.address().port}`;
  const browser = await chromium.launch();
  const t0s = []; board.scenes.reduce((t, s) => (t0s.push(t), t + s.dur), 0);
  const dur = board.scenes.reduce((t, s) => t + s.dur, 0);
  console.log(`${board.id} · ${fmt} · ${board.scenes.length} scenes · ${dur.toFixed(1)}s`);
  const results = new Array(board.scenes.length);
  let next = 0;
  await Promise.all(Array.from({ length: Math.max(1, o.jobs) }, async () => {
    while (next < board.scenes.length) {
      const i = next++;
      const t = Date.now();
      results[i] = await renderScene(browser, base, board, board.scenes[i], i, t0s[i], fmt, work);
      console.log(`  scene ${i} ${board.scenes[i].type} ${board.scenes[i].dur}s  ${((Date.now() - t) / 1000).toFixed(1)}s`);
    }
  }));
  await browser.close(); srv.close();

  const list = path.join(work, 'concat.txt');
  fs.writeFileSync(list, results.map(r => `file '${r.out.replace(/\\/g, '/').replace(/'/g, "'\\''")}'`).join('\n'));
  const silent = path.join(work, 'silent.mp4');
  await run('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', list, '-c', 'copy', silent]);
  const final = path.join(outDir, `${board.id}_${fmt}.mp4`);
  await mixAudio(board, boardDir, silent, final, dur);
  if (results[0].poster) fs.writeFileSync(path.join(outDir, `${board.id}_${fmt}_poster.png`), results[0].poster);
  if (!o.keep) fs.rmSync(work, { recursive: true, force: true });
  console.log('done ->', final);
}

main().catch(e => { console.error(e.stack || e); process.exit(1); });
