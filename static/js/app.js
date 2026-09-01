/* Minecraft Server Status — dashboard front end.
 * No dependencies: charts are hand-rolled SVG so the page stays offline-capable.
 * All server-supplied text goes in through textContent, never innerHTML. */
(() => {
  'use strict';

  const HISTORY_KEY = 'mcstatus.history.v1';
  const RECENTS_KEY = 'mcstatus.recents.v1';
  const THEME_KEY = 'mcstatus.theme';
  const MAX_SAMPLES = 600;
  const MAX_TRACKED_SERVERS = 10;
  const MAX_RECENTS = 6;

  // Minecraft's named colors. `black` is nudged up so it stays legible on the
  // MOTD plate — everything else is the in-game hex.
  const MC_COLORS = {
    black: '#4c4c52', dark_blue: '#0000aa', dark_green: '#00aa00', dark_aqua: '#00aaaa',
    dark_red: '#aa0000', dark_purple: '#aa00aa', gold: '#ffaa00', gray: '#aaaaaa',
    dark_gray: '#555555', blue: '#5555ff', green: '#55ff55', aqua: '#55ffff',
    red: '#ff5555', light_purple: '#ff55ff', yellow: '#ffff55', white: '#ffffff'
  };

  const ERROR_TITLES = {
    dns_failure: 'Host not found',
    timeout: 'No response',
    refused: 'Connection refused',
    unreachable: 'Unreachable',
    protocol_error: 'Unexpected response',
    invalid_host: 'Invalid address',
    blocked_host: 'Address not allowed',
    rate_limited: 'Too many requests'
  };

  const $ = (id) => document.getElementById(id);
  const SVG_NS = 'http://www.w3.org/2000/svg';

  const el = {
    form: $('searchForm'), input: $('serverInput'), edition: $('editionSelect'),
    checkBtn: $('checkBtn'), progress: $('progress'), results: $('results'),
    empty: $('emptyState'), errorNotice: $('errorNotice'), errorTitle: $('errorTitle'),
    errorBody: $('errorBody'), serverCard: $('serverCard'), serverIcon: $('serverIcon'),
    serverName: $('serverName'), serverMeta: $('serverMeta'), statusPill: $('statusPill'),
    motd: $('motd'), tiles: $('tiles'), historyCard: $('historyCard'),
    historyHint: $('historyHint'), charts: $('charts'), historyTable: $('historyTable'),
    rangeGroup: $('rangeGroup'), autoRefresh: $('autoRefresh'), tableToggle: $('tableToggle'),
    clearHistory: $('clearHistory'), playersCard: $('playersCard'), playersHint: $('playersHint'),
    playersList: $('playersList'), detailsCard: $('detailsCard'), detailsBody: $('detailsBody'),
    recents: $('recents'), themeToggle: $('themeToggle')
  };

  const state = {
    key: null,          // history key for the server on screen
    data: null,         // last API payload
    range: 0,           // seconds; 0 = all
    tableView: false,
    timer: null,
    inFlight: false
  };

  /* ---------------------------------------------------------------- utils */

  const svgEl = (name, attrs = {}) => {
    const node = document.createElementNS(SVG_NS, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
  };

  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  };

  const readStore = (key, fallback) => {
    try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
  };
  const writeStore = (key, value) => {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
  };

  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  const compact = (value) => {
    if (value === null || value === undefined || Number.isNaN(value)) return '—';
    if (Math.abs(value) >= 1e6) return (value / 1e6).toFixed(value % 1e6 === 0 ? 0 : 1) + 'M';
    if (Math.abs(value) >= 10000) return (value / 1000).toFixed(value % 1000 === 0 ? 0 : 1) + 'K';
    return value.toLocaleString();
  };
  const full = (value) => (value === null || value === undefined ? '—' : value.toLocaleString());
  const clockTime = (ts) => new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const stampTime = (ts) => new Date(ts).toLocaleString([], {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit'
  });

  /* ------------------------------------------------------------- history */

  const historyKey = (server, edition) => `${edition}|${server.toLowerCase()}`;

  function loadHistory(key) {
    const all = readStore(HISTORY_KEY, {});
    return Array.isArray(all[key]) ? all[key] : [];
  }

  function pushSample(key, sample) {
    const all = readStore(HISTORY_KEY, {});
    const list = Array.isArray(all[key]) ? all[key] : [];
    list.push(sample);
    all[key] = list.slice(-MAX_SAMPLES);

    const keys = Object.keys(all);
    if (keys.length > MAX_TRACKED_SERVERS) {
      keys
        .map((k) => [k, all[k].length ? all[k][all[k].length - 1][0] : 0])
        .sort((a, b) => a[1] - b[1])
        .slice(0, keys.length - MAX_TRACKED_SERVERS)
        .forEach(([k]) => delete all[k]);
    }
    writeStore(HISTORY_KEY, all);
    return all[key];
  }

  function clearHistoryFor(key) {
    const all = readStore(HISTORY_KEY, {});
    delete all[key];
    writeStore(HISTORY_KEY, all);
  }

  // Samples are stored compactly: [timestamp, online, latencyMs, playersOnline]
  const toSample = (row) => ({ t: row[0], online: !!row[1], latency: row[2], players: row[3] });

  function visibleSamples() {
    if (!state.key) return [];
    const rows = loadHistory(state.key).map(toSample);
    if (!state.range) return rows;
    const cutoff = Date.now() - state.range * 1000;
    return rows.filter((row) => row.t >= cutoff);
  }

  /* --------------------------------------------------------------- charts */

  function niceTicks(max, count = 4) {
    if (!(max > 0)) return [0, 1];
    const raw = max / count;
    const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) || magnitude * 10;
    const ticks = [];
    for (let value = 0; value <= max + step / 2; value += step) ticks.push(value);
    return ticks;
  }

  /**
   * One measure, one axis, one series — a line chart with an area wash,
   * a crosshair tooltip, a direct end label, and keyboard readout.
   */
  function renderLineChart(mount, config) {
    const { samples, accessor, color, unit, format = full, emptyText } = config;
    mount.textContent = '';

    const figure = node('figure', 'chart-figure');
    const caption = node('figcaption');
    caption.appendChild(node('div', 'chart-title', config.title));
    caption.appendChild(node('div', 'chart-sub', config.subtitle));
    figure.appendChild(caption);

    const points = samples.map((s) => ({ t: s.t, v: accessor(s) })).filter((p) => p.t);
    const known = points.filter((p) => typeof p.v === 'number' && !Number.isNaN(p.v));
    if (known.length === 0) {
      figure.appendChild(node('div', 'chart-empty', emptyText));
      mount.appendChild(figure);
      return;
    }

    const width = Math.max(280, mount.clientWidth || 340);
    const height = 188;
    const pad = { top: 18, right: 56, bottom: 26, left: 46 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;

    const t0 = points[0].t;
    const t1 = points[points.length - 1].t;
    const span = Math.max(t1 - t0, 1);
    const maxValue = Math.max(...known.map((p) => p.v));
    const ticks = niceTicks(maxValue * 1.12 || 1);
    const yMax = ticks[ticks.length - 1] || 1;

    const x = (t) => pad.left + ((t - t0) / span) * plotW;
    const y = (v) => pad.top + plotH - (v / yMax) * plotH;

    const svg = svgEl('svg', {
      viewBox: `0 0 ${width} ${height}`, width, height,
      role: 'img', 'aria-label': `${config.title}: ${config.subtitle}`
    });
    svg.style.width = '100%';
    svg.style.height = `${height}px`;

    const grid = css('--grid');
    const muted = css('--text-muted');
    const surface = css('--surface');

    // Gridlines: solid hairlines, one step off the surface.
    ticks.forEach((tick) => {
      svg.appendChild(svgEl('line', {
        x1: pad.left, x2: pad.left + plotW, y1: y(tick), y2: y(tick),
        stroke: tick === 0 ? css('--axis') : grid, 'stroke-width': 1
      }));
      const label = svgEl('text', {
        x: pad.left - 8, y: y(tick) + 4, 'text-anchor': 'end',
        fill: muted, 'font-size': 10.5, 'font-variant-numeric': 'tabular-nums'
      });
      label.textContent = compact(tick);
      svg.appendChild(label);
    });

    // Split into contiguous segments so downtime shows as a gap, not a lie.
    const segments = [];
    let current = [];
    points.forEach((point) => {
      if (typeof point.v === 'number' && !Number.isNaN(point.v)) current.push(point);
      else if (current.length) { segments.push(current); current = []; }
    });
    if (current.length) segments.push(current);

    segments.forEach((segment) => {
      const line = segment.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`).join(' ');
      if (segment.length > 1) {
        svg.appendChild(svgEl('path', {
          d: `${line} L${x(segment[segment.length - 1].t).toFixed(1)} ${y(0)} L${x(segment[0].t).toFixed(1)} ${y(0)} Z`,
          fill: color, 'fill-opacity': 0.10, stroke: 'none'
        }));
      }
      svg.appendChild(svgEl('path', {
        d: segment.length > 1 ? line : `${line} l0.01 0`,
        fill: 'none', stroke: color, 'stroke-width': 2,
        'stroke-linecap': 'round', 'stroke-linejoin': 'round'
      }));
    });

    // Outage markers: a low tick where a check found the server offline.
    samples.forEach((sample) => {
      if (sample.online) return;
      svg.appendChild(svgEl('line', {
        x1: x(sample.t), x2: x(sample.t), y1: y(0), y2: y(0) - 6,
        stroke: css('--critical'), 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-opacity': 0.55
      }));
    });

    // Direct end label — the one labelled value; the axis and tooltip carry the rest.
    const last = known[known.length - 1];
    svg.appendChild(svgEl('circle', {
      cx: x(last.t), cy: y(last.v), r: 4, fill: color, stroke: surface, 'stroke-width': 2
    }));
    const endLabel = svgEl('text', {
      x: Math.min(x(last.t) + 10, width - 4), y: y(last.v) + 4,
      fill: css('--text-primary'), 'font-size': 12, 'font-weight': 600
    });
    endLabel.textContent = format(last.v) + (unit || '');
    svg.appendChild(endLabel);

    // x-axis: first and last timestamps only.
    const axisText = (xPos, anchor, text) => {
      const label = svgEl('text', {
        x: xPos, y: height - 6, 'text-anchor': anchor, fill: muted,
        'font-size': 10.5, 'font-variant-numeric': 'tabular-nums'
      });
      label.textContent = text;
      return label;
    };
    svg.appendChild(axisText(pad.left, 'start', clockTime(t0)));
    if (span > 60000) svg.appendChild(axisText(pad.left + plotW, 'end', clockTime(t1)));

    // Hover layer: crosshair + tooltip, snapped to the nearest sample.
    const crosshair = svgEl('line', {
      y1: pad.top, y2: pad.top + plotH, stroke: css('--axis'), 'stroke-width': 1, opacity: 0
    });
    const marker = svgEl('circle', { r: 4.5, fill: color, stroke: surface, 'stroke-width': 2, opacity: 0 });
    svg.appendChild(crosshair);
    svg.appendChild(marker);

    const hit = svgEl('rect', {
      x: pad.left, y: pad.top, width: plotW, height: plotH, fill: 'transparent'
    });
    svg.appendChild(hit);

    const tooltip = node('div', 'tooltip');
    const tipValue = node('div', 'tooltip-value');
    const tipRow = node('div', 'tooltip-row');
    const tipKey = node('span', 'tooltip-key');
    tipKey.style.background = color;
    tipRow.appendChild(tipKey);
    tipRow.appendChild(node('span', null, config.title));
    const tipTime = node('div', 'tooltip-time');
    tooltip.append(tipValue, tipRow, tipTime);

    let active = -1;
    const show = (index) => {
      const point = points[index];
      if (!point) return;
      active = index;
      crosshair.setAttribute('x1', x(point.t));
      crosshair.setAttribute('x2', x(point.t));
      crosshair.setAttribute('opacity', 1);
      const hasValue = typeof point.v === 'number' && !Number.isNaN(point.v);
      marker.setAttribute('opacity', hasValue ? 1 : 0);
      if (hasValue) {
        marker.setAttribute('cx', x(point.t));
        marker.setAttribute('cy', y(point.v));
      }
      tipValue.textContent = hasValue ? format(point.v) + (unit || '') : 'Offline';
      tipTime.textContent = stampTime(point.t);
      tooltip.dataset.visible = 'true';
      const left = Math.min(Math.max(x(point.t), 70), width - 70);
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${(hasValue ? y(point.v) : pad.top + plotH) - 12}px`;
    };
    const hide = () => {
      active = -1;
      crosshair.setAttribute('opacity', 0);
      marker.setAttribute('opacity', 0);
      tooltip.dataset.visible = 'false';
    };
    const nearest = (clientX) => {
      const rect = svg.getBoundingClientRect();
      const scale = width / rect.width;
      const target = t0 + ((clientX - rect.left) * scale - pad.left) / plotW * span;
      let best = 0;
      points.forEach((point, index) => {
        if (Math.abs(point.t - target) < Math.abs(points[best].t - target)) best = index;
      });
      return best;
    };

    hit.addEventListener('pointermove', (event) => show(nearest(event.clientX)));
    hit.addEventListener('pointerleave', hide);
    svg.setAttribute('tabindex', '0');
    svg.addEventListener('focus', () => show(points.length - 1));
    svg.addEventListener('blur', hide);
    svg.addEventListener('keydown', (event) => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      event.preventDefault();
      const start = active === -1 ? points.length - 1 : active;
      show(Math.min(points.length - 1, Math.max(0, start + (event.key === 'ArrowRight' ? 1 : -1))));
    });

    const plot = node('div');
    plot.style.position = 'relative';
    plot.appendChild(svg);
    plot.appendChild(tooltip);
    figure.appendChild(plot);
    mount.appendChild(figure);
  }

  function sparkline(mount, values, color) {
    mount.textContent = '';
    const points = values.filter((v) => typeof v === 'number' && !Number.isNaN(v));
    if (points.length < 2) return;
    const width = 120;
    const height = 26;
    const max = Math.max(...points, 1);
    const min = Math.min(...points, 0);
    const span = Math.max(max - min, 1);
    const x = (i) => (i / (points.length - 1)) * width;
    const y = (v) => height - 3 - ((v - min) / span) * (height - 6);
    const svg = svgEl('svg', { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'none', 'aria-hidden': 'true' });
    svg.appendChild(svgEl('path', {
      d: points.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' '),
      fill: 'none', stroke: color, 'stroke-width': 1.5, 'stroke-opacity': 0.45,
      'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'vector-effect': 'non-scaling-stroke'
    }));
    svg.appendChild(svgEl('circle', {
      cx: x(points.length - 1), cy: y(points[points.length - 1]), r: 2.5, fill: color
    }));
    mount.appendChild(svg);
  }

  /* --------------------------------------------------------------- render */

  function renderMotd(tokens) {
    el.motd.textContent = '';
    if (!tokens || !tokens.length) { el.motd.hidden = true; return; }
    tokens.forEach((token) => {
      const span = node('span', null, token.text);
      const color = token.color;
      if (color && MC_COLORS[color]) span.style.color = MC_COLORS[color];
      else if (typeof color === 'string' && /^#[0-9a-f]{6}$/i.test(color)) span.style.color = color;
      ['bold', 'italic', 'underlined', 'strikethrough', 'obfuscated'].forEach((style) => {
        if (token[style]) span.classList.add(`mc-${style}`);
      });
      el.motd.appendChild(span);
    });
    el.motd.hidden = false;
  }

  function statusPill(data) {
    el.statusPill.textContent = '';
    const online = !!data.online;
    el.statusPill.className = `pill ${online ? 'pill-online' : 'pill-offline'}`;
    const icon = svgEl('svg', {
      viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
      'stroke-width': '2.4', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true'
    });
    icon.appendChild(svgEl('path', { d: online ? 'M4 12.5 9.5 18 20 6.5' : 'M6 6l12 12M18 6 6 18' }));
    el.statusPill.appendChild(icon);
    el.statusPill.appendChild(node('span', null, online ? 'Online' : 'Offline'));
  }

  function copyButton(text) {
    const button = node('button', 'copy-btn');
    button.type = 'button';
    const label = node('span', null, text);
    const icon = svgEl('svg', {
      viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
      'stroke-width': '1.9', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true'
    });
    icon.appendChild(svgEl('rect', { x: 9, y: 9, width: 12, height: 12, rx: 2 }));
    icon.appendChild(svgEl('path', { d: 'M5 15V5a2 2 0 0 1 2-2h10' }));
    button.append(label, icon);
    button.title = `Copy ${text}`;
    button.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(text); } catch { return; }
      const previous = label.textContent;
      label.textContent = 'Copied';
      setTimeout(() => { label.textContent = previous; }, 1200);
    });
    return button;
  }

  function renderServerCard(data) {
    el.serverIcon.textContent = '';
    if (data.icon) {
      const image = document.createElement('img');
      image.src = data.icon;
      image.alt = '';
      el.serverIcon.appendChild(image);
    } else {
      const glyph = svgEl('svg', {
        viewBox: '0 0 24 24', width: 22, height: 22, fill: 'none', stroke: 'currentColor',
        'stroke-width': '1.6', 'stroke-linecap': 'round', 'stroke-linejoin': 'round'
      });
      glyph.appendChild(svgEl('rect', { x: 3, y: 4, width: 18, height: 16, rx: 3 }));
      glyph.appendChild(svgEl('path', { d: 'M3 10h18' }));
      el.serverIcon.appendChild(glyph);
    }

    el.serverName.textContent = data.hostname || '—';

    el.serverMeta.textContent = '';
    const address = data.ip
      ? (data.ip.includes(':') ? `[${data.ip}]:${data.port}` : `${data.ip}:${data.port}`)
      : null;
    if (address) el.serverMeta.appendChild(copyButton(address));

    const facts = [];
    if (data.edition) facts.push(data.edition === 'bedrock' ? 'Bedrock Edition' : 'Java Edition');
    if (data.srv_record) facts.push(`SRV → ${data.srv_record.target}:${data.srv_record.port}`);
    if (data.mods) facts.push(`${data.mods.count} mods (${data.mods.type})`);
    if (data.cached) facts.push('cached');
    facts.forEach((fact, index) => {
      if (index || address) el.serverMeta.appendChild(node('span', 'dot', '·'));
      el.serverMeta.appendChild(node('span', null, fact));
    });

    statusPill(data);
    renderMotd(data.motd && data.motd.tokens);
    el.serverCard.hidden = false;
  }

  function tile(label, valueNode, sub) {
    const wrap = node('div', 'tile');
    wrap.appendChild(node('div', 'tile-label', label));
    wrap.appendChild(valueNode);
    if (sub) wrap.appendChild(node('div', 'tile-sub', sub));
    return wrap;
  }

  function valueNode(value, unit, small) {
    const wrap = node('div', `tile-value${small ? ' sm' : ''}`);
    wrap.appendChild(document.createTextNode(value));
    if (unit) wrap.appendChild(node('span', 'unit', unit));
    return wrap;
  }

  function renderTiles(data, samples) {
    el.tiles.textContent = '';
    if (!data.online) { el.tiles.hidden = true; return; }

    const players = data.players || {};
    const playersTile = tile(
      'Players online',
      valueNode(compact(players.online), players.max ? ` / ${compact(players.max)}` : ''),
      players.max ? `${Math.round((players.online / players.max) * 100)}% of capacity` : null
    );
    if (players.max) {
      const meter = node('div', 'meter');
      const fill = node('span');
      fill.style.width = `${Math.min(100, (players.online / players.max) * 100)}%`;
      meter.appendChild(fill);
      playersTile.appendChild(meter);
    }
    el.tiles.appendChild(playersTile);

    const latencyTile = tile(
      'Latency',
      valueNode(data.latency_ms !== null && data.latency_ms !== undefined ? Math.round(data.latency_ms) : '—', ' ms'),
      samples.length > 1 ? `${samples.length} checks recorded` : 'ping round trip'
    );
    const spark = node('div', 'tile-spark');
    latencyTile.appendChild(spark);
    el.tiles.appendChild(latencyTile);
    sparkline(spark, samples.slice(-12).map((s) => s.latency), css('--series-latency'));

    el.tiles.appendChild(tile('Version', valueNode(data.version || '—', '', true),
      data.protocol ? `Protocol ${data.protocol}` : null));

    const online = samples.filter((s) => s.online).length;
    el.tiles.appendChild(tile(
      'Availability',
      valueNode(samples.length ? `${Math.round((online / samples.length) * 100)}` : '—', '%', false),
      samples.length ? `${online}/${samples.length} checks online` : 'from this browser'
    ));

    el.tiles.hidden = false;
  }

  function renderHistoryTable(samples) {
    el.historyTable.textContent = '';
    const table = node('table');
    const head = node('thead');
    const headRow = node('tr');
    [['Time', ''], ['Status', ''], ['Latency', 'num'], ['Players', 'num']].forEach(([label, cls]) => {
      headRow.appendChild(node('th', cls, label));
    });
    head.appendChild(headRow);
    const body = node('tbody');
    samples.slice().reverse().slice(0, 60).forEach((sample) => {
      const row = node('tr');
      row.appendChild(node('td', null, stampTime(sample.t)));
      row.appendChild(node('td', null, sample.online ? 'Online' : 'Offline'));
      row.appendChild(node('td', 'num', sample.latency === null || sample.latency === undefined
        ? '—' : `${Math.round(sample.latency)} ms`));
      row.appendChild(node('td', 'num', full(sample.players)));
      body.append(row);
    });
    table.append(head, body);
    el.historyTable.appendChild(table);
  }

  function renderHistory() {
    const samples = visibleSamples();
    el.historyCard.hidden = false;

    const online = samples.filter((s) => s.online);
    const latencies = online.map((s) => s.latency).filter((v) => typeof v === 'number');
    const average = latencies.length
      ? Math.round(latencies.reduce((sum, v) => sum + v, 0) / latencies.length) : null;
    el.historyHint.textContent = samples.length
      ? `${samples.length} check${samples.length === 1 ? '' : 's'}${average !== null ? ` · avg ${average} ms` : ''}`
      : 'recorded in this browser';

    el.charts.textContent = '';
    const latencyMount = node('div');
    const playersMount = node('div');
    el.charts.append(latencyMount, playersMount);

    renderLineChart(latencyMount, {
      samples,
      accessor: (s) => (s.online ? s.latency : null),
      color: css('--series-latency'),
      title: 'Latency',
      subtitle: latencies.length > 1
        ? `min ${Math.round(Math.min(...latencies))} ms · max ${Math.round(Math.max(...latencies))} ms`
        : 'ping round trip, in milliseconds',
      unit: ' ms',
      format: (v) => String(Math.round(v)),
      emptyText: 'No latency recorded yet. Check again to start a series.'
    });

    renderLineChart(playersMount, {
      samples,
      accessor: (s) => (s.online ? s.players : null),
      color: css('--series-players'),
      title: 'Players online',
      subtitle: online.length > 1 ? `over ${online.length} online checks` : 'players connected at each check',
      format: full,
      emptyText: 'No player counts recorded yet.'
    });

    if (state.tableView) renderHistoryTable(samples);
    el.historyTable.hidden = !state.tableView;
  }

  function renderPlayers(data) {
    const sample = (data.players && data.players.sample) || [];
    el.playersList.textContent = '';
    if (!sample.length) { el.playersCard.hidden = true; return; }
    el.playersHint.textContent = `${sample.length} of ${full(data.players.online)} shown by the server`;
    sample.forEach((player) => {
      const chip = node('span', 'player');
      chip.appendChild(node('span', 'avatar', (player.name || '?').slice(0, 1).toUpperCase()));
      chip.appendChild(node('span', null, player.name));
      el.playersList.appendChild(chip);
    });
    el.playersCard.hidden = false;
  }

  function renderDetails(data) {
    el.detailsBody.textContent = '';
    const rows = [
      ['Hostname', data.hostname],
      ['Resolved IP', data.ip],
      ['Port', data.port],
      ['Edition', data.edition === 'bedrock' ? 'Bedrock' : 'Java'],
      ['Version', data.version],
      ['Protocol', data.protocol],
      ['Software', data.software],
      ['SRV record', data.srv_record
        ? `${data.srv_record.target}:${data.srv_record.port} (priority ${data.srv_record.priority})`
        : 'none'],
      ['Latency', data.latency_ms !== null && data.latency_ms !== undefined ? `${data.latency_ms} ms` : null],
      ['Gamemode', data.gamemode],
      ['World', data.map],
      ['Mods', data.mods ? `${data.mods.count} (${data.mods.type})` : null],
      ['MOTD (plain text)', data.motd && data.motd.clean],
      ['Checked at', data.checked_at ? stampTime(data.checked_at * 1000) : null],
      ['Response', `${data.query_time_ms} ms${data.cached ? ' (served from cache)' : ''}`]
    ];
    rows.forEach(([label, value]) => {
      if (value === null || value === undefined || value === '') return;
      const row = node('tr');
      row.appendChild(node('td', null, label));
      row.appendChild(node('td', null, String(value)));
      el.detailsBody.appendChild(row);
    });
    el.detailsCard.hidden = false;
  }

  function showError(data) {
    el.errorTitle.textContent = ERROR_TITLES[data.error_code] || 'Check failed';
    el.errorBody.textContent = data.error || 'The server could not be reached.';
    el.errorNotice.hidden = false;
  }

  /* --------------------------------------------------------------- recents */

  function renderRecents() {
    const recents = readStore(RECENTS_KEY, []);
    el.recents.querySelectorAll('.chip').forEach((chip) => chip.remove());
    if (!recents.length) { el.recents.hidden = true; return; }
    recents.forEach((entry) => {
      const chip = node('button', 'chip', entry.server);
      chip.type = 'button';
      chip.addEventListener('click', () => {
        el.input.value = entry.server;
        el.edition.value = entry.edition || 'auto';
        scheduleRefresh();
        check();
      });
      el.recents.appendChild(chip);
    });
    el.recents.hidden = false;
  }

  function rememberRecent(server, edition) {
    const recents = readStore(RECENTS_KEY, [])
      .filter((entry) => entry.server.toLowerCase() !== server.toLowerCase());
    recents.unshift({ server, edition });
    writeStore(RECENTS_KEY, recents.slice(0, MAX_RECENTS));
    renderRecents();
  }

  /* ----------------------------------------------------------------- check */

  async function check({ silent = false } = {}) {
    const server = el.input.value.trim();
    if (!server || state.inFlight) return;
    const edition = el.edition.value;

    state.inFlight = true;
    el.checkBtn.disabled = true;
    if (!silent) el.progress.hidden = false;
    el.results.dataset.loading = 'true';

    const url = `/api/status?server=${encodeURIComponent(server)}&edition=${encodeURIComponent(edition)}`
      + (silent ? '' : '&refresh=1');

    let data;
    try {
      const response = await fetch(url, { headers: { Accept: 'application/json' } });
      data = await response.json();
    } catch (error) {
      data = { online: false, hostname: server, edition,
               error: 'Could not reach the status API from this browser.', error_code: 'network' };
    } finally {
      state.inFlight = false;
      el.checkBtn.disabled = false;
      el.progress.hidden = true;
      el.results.dataset.loading = 'false';
    }

    state.data = data;
    el.empty.hidden = true;

    const isBadRequest = ['invalid_host', 'blocked_host', 'missing_parameter',
                          'invalid_parameter', 'rate_limited', 'network'].includes(data.error_code);
    if (isBadRequest) {
      showError(data);
      el.serverCard.hidden = el.tiles.hidden = el.historyCard.hidden = true;
      el.playersCard.hidden = el.detailsCard.hidden = true;
      return;
    }

    el.errorNotice.hidden = true;
    if (!data.online && data.error) showError(data);

    state.key = historyKey(server, data.edition || edition);
    if (!data.cached) {
      pushSample(state.key, [
        Date.now(), data.online ? 1 : 0,
        data.online && typeof data.latency_ms === 'number' ? Math.round(data.latency_ms) : null,
        data.online && data.players ? data.players.online ?? null : null
      ]);
    }

    renderServerCard(data);
    renderTiles(data, loadHistory(state.key).map(toSample));
    renderHistory();
    renderPlayers(data);
    renderDetails(data);
    rememberRecent(server, edition);

    const params = new URLSearchParams({ server });
    if (edition !== 'auto') params.set('edition', edition);
    history.replaceState(null, '', `?${params}`);
  }

  /* ---------------------------------------------------------------- events */

  el.form.addEventListener('submit', (event) => {
    event.preventDefault();
    scheduleRefresh();
    check();
  });

  el.rangeGroup.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-range]');
    if (!button) return;
    state.range = Number(button.dataset.range);
    el.rangeGroup.querySelectorAll('button').forEach((other) => {
      other.setAttribute('aria-pressed', other === button ? 'true' : 'false');
    });
    renderHistory();
  });

  el.tableToggle.addEventListener('click', () => {
    state.tableView = !state.tableView;
    el.tableToggle.setAttribute('aria-pressed', String(state.tableView));
    el.tableToggle.textContent = state.tableView ? 'Hide table' : 'Table view';
    renderHistory();
  });

  el.clearHistory.addEventListener('click', () => {
    if (!state.key) return;
    clearHistoryFor(state.key);
    if (state.data) renderTiles(state.data, []);
    renderHistory();
  });

  function scheduleRefresh() {
    clearInterval(state.timer);
    const seconds = Number(el.autoRefresh.value);
    if (!seconds) return;
    state.timer = setInterval(() => {
      if (document.hidden || state.inFlight) return;
      check({ silent: true });
    }, seconds * 1000);
  }
  el.autoRefresh.addEventListener('change', scheduleRefresh);

  let resizeFrame = null;
  new ResizeObserver(() => {
    if (el.historyCard.hidden) return;
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(renderHistory);
  }).observe(el.charts);

  document.addEventListener('keydown', (event) => {
    if (event.key === '/' && document.activeElement !== el.input) {
      event.preventDefault();
      el.input.focus();
      el.input.select();
    }
  });

  /* ----------------------------------------------------------------- theme */

  function applyTheme(theme) {
    if (theme) document.documentElement.setAttribute('data-theme', theme);
    else document.documentElement.removeAttribute('data-theme');
    if (!el.historyCard.hidden) renderHistory();
  }
  applyTheme(readStore(THEME_KEY, null));
  el.themeToggle.addEventListener('click', () => {
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark'
      || (!document.documentElement.hasAttribute('data-theme')
          && matchMedia('(prefers-color-scheme: dark)').matches);
    const next = isDark ? 'light' : 'dark';
    writeStore(THEME_KEY, next);
    applyTheme(next);
  });

  /* ------------------------------------------------------------------ boot */

  renderRecents();
  const params = new URLSearchParams(location.search);
  const initial = params.get('server') || readStore(RECENTS_KEY, [])[0]?.server || '';
  if (params.get('edition')) el.edition.value = params.get('edition');
  if (initial) {
    el.input.value = initial;
    if (params.get('server')) { scheduleRefresh(); check(); }
  }
})();
