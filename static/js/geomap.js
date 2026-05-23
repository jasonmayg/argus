/* ============================================================
   ARGUS — Geospatial Risk Map
   Renders an SVG world map on the home page with:
     • Country fill colour keyed to risk level (High/Medium/Low)
     • Focus countries (those with a native source crawler upstream
       in AI_Treasury_Project) highlighted with border + label
     • Circular badge showing alert count per country
     • Hover tooltip: country name, risk level, top news headline
   Uses TopoJSON world-110m (loaded from CDN) + a minimal
   Mercator projection implemented without D3.
   ============================================================ */

// ── Focus countries: ISO-3 → display label.
// Membership criterion: the upstream data pipeline currently
// publishes a dedicated regulator crawler for this country
// (fed/treasury, boe/fca, boj, bank_of_canada, …). Countries
// that only show up via cross-references in other sources stay
// off this list so the map doesn't promise coverage we don't
// actually have. Add a country back when its native source goes
// live in AI_Treasury_Project.
const _FOCUS = {
    USA: 'US',
    CAN: 'Canada',
    GBR: 'UK',
    JPN: 'Japan',
    IND: 'India',
};

// ── Initial zoom view — derived from `_FOCUS` (Object.keys preserves
// insertion order for string keys per ES2015+). Tying the two together
// at the variable level so the next person who edits `_FOCUS` can't
// accidentally let the initial viewport drift out of sync with the
// focus list. Override here only if some focus country is so far off
// the main viewport that it would shrink the others too much (none
// today; revisit if Brazil/Australia ever get added back).
const _INIT_VIEW_KEYS = Object.keys(_FOCUS);

// ── Country name → ISO-3166 alpha-3 lookup
const _ISO3 = {
    'Afghanistan':'AFG','Albania':'ALB','Algeria':'DZA','Argentina':'ARG',
    'Australia':'AUS','Austria':'AUT','Azerbaijan':'AZE','Bangladesh':'BGD',
    'Belarus':'BLR','Belgium':'BEL','Bolivia':'BOL','Brazil':'BRA',
    'Bulgaria':'BGR','Cambodia':'KHM','Cameroon':'CMR','Canada':'CAN',
    'Chile':'CHL','China':'CHN','Colombia':'COL','Congo':'COD',
    'Croatia':'HRV','Cuba':'CUB','Czech Republic':'CZE','Denmark':'DNK',
    'Ecuador':'ECU','Egypt':'EGY','Ethiopia':'ETH','Finland':'FIN',
    'France':'FRA','Germany':'DEU','Ghana':'GHA','Greece':'GRC',
    'Guatemala':'GTM','Hungary':'HUN','India':'IND','Indonesia':'IDN',
    'Iran':'IRN','Iraq':'IRQ','Ireland':'IRL','Israel':'ISR',
    'Italy':'ITA','Japan':'JPN','Jordan':'JOR','Kazakhstan':'KAZ',
    'Kenya':'KEN','North Korea':'PRK','South Korea':'KOR','Korea':'KOR',
    'Kuwait':'KWT','Lebanon':'LBN','Libya':'LBY','Malaysia':'MYS',
    'Mexico':'MEX','Morocco':'MAR','Mozambique':'MOZ','Myanmar':'MMR',
    'Netherlands':'NLD','New Zealand':'NZL','Nigeria':'NGA','Norway':'NOR',
    'Pakistan':'PAK','Peru':'PER','Philippines':'PHL','Poland':'POL',
    'Portugal':'PRT','Romania':'ROU','Russia':'RUS','Saudi Arabia':'SAU',
    'Serbia':'SRB','Singapore':'SGP','Somalia':'SOM','South Africa':'ZAF',
    'Spain':'ESP','Sri Lanka':'LKA','Sudan':'SDN','Sweden':'SWE',
    'Switzerland':'CHE','Syria':'SYR','Taiwan':'TWN','Thailand':'THA',
    'Turkey':'TUR','Turkiye':'TUR','Ukraine':'UKR',
    'United Arab Emirates':'ARE','UAE':'ARE',
    'United Kingdom':'GBR','UK':'GBR',
    'United States':'USA','US':'USA','USA':'USA',
    'Venezuela':'VEN','Vietnam':'VNM','Yemen':'YEM','Zimbabwe':'ZWE',
};

// ── Centroid (lon, lat) for badge + label placement
const _CENTROIDS = {
    AFG:[67.7,33.9],ALB:[20.2,41.2],DZA:[3.0,28.0],ARG:[-64.0,-34.0],
    AUS:[134.0,-25.0],AUT:[14.6,47.7],AZE:[47.4,40.4],BGD:[90.4,23.7],
    BLR:[28.0,53.5],BEL:[4.5,50.5],BOL:[-65.0,-17.0],BRA:[-52.0,-10.0],
    BGR:[25.5,42.7],KHM:[104.9,12.6],CAN:[-96.0,56.0],CHL:[-71.0,-35.0],
    CHN:[104.2,35.9],COL:[-74.3,4.6],COD:[24.0,-3.0],
    HRV:[16.4,45.2],CUB:[-79.5,22.0],CZE:[15.5,49.8],DNK:[10.0,56.0],
    ECU:[-78.0,-2.0],EGY:[30.8,26.8],ETH:[40.5,9.0],FIN:[26.0,64.0],
    FRA:[2.3,46.2],DEU:[10.5,51.2],GHA:[-1.0,8.0],GRC:[22.0,39.0],
    GTM:[-90.2,15.8],HUN:[19.5,47.2],IND:[80.0,20.6],IDN:[113.9,-0.8],
    IRN:[53.7,32.4],IRQ:[44.4,33.2],IRL:[-8.2,53.2],ISR:[34.9,31.5],
    ITA:[12.6,42.8],JPN:[138.0,36.5],JOR:[36.2,31.2],KAZ:[68.0,48.0],
    KEN:[37.9,0.0],PRK:[127.5,40.3],KOR:[127.8,36.5],KWT:[47.5,29.3],
    LBN:[35.9,33.9],LBY:[17.2,26.3],MYS:[110.5,4.2],MEX:[-102.5,23.9],
    MAR:[-7.1,31.8],MOZ:[35.5,-18.7],MMR:[96.3,17.1],NLD:[5.3,52.4],
    NZL:[172.5,-41.5],NGA:[8.7,9.1],NOR:[10.8,60.5],PAK:[69.3,30.4],
    PER:[-76.0,-10.0],PHL:[122.0,12.9],POL:[20.0,52.1],PRT:[-8.2,39.6],
    ROU:[25.0,45.9],RUS:[100.0,60.0],SAU:[44.6,24.2],SRB:[21.0,44.0],
    SGP:[103.8,1.4],SOM:[46.2,6.1],ZAF:[25.1,-29.0],ESP:[-3.7,40.4],
    LKA:[80.7,7.9],SDN:[30.2,15.9],SWE:[18.6,62.0],CHE:[8.2,46.8],
    SYR:[38.5,35.0],TWN:[120.9,24.0],THA:[101.0,15.9],TUR:[35.3,39.1],
    UKR:[32.0,49.0],ARE:[54.0,24.0],GBR:[-1.8,52.5],USA:[-101.0,39.5],
    VEN:[-66.6,8.0],VNM:[107.8,16.5],YEM:[47.6,15.6],ZWE:[29.9,-20.1],
};

// ── Risk colour palette (3 levels: High = red, Medium = amber, Low = green)
const _RISK_FILL = {
    High:   '#FEE2E2',   // red-100   — danger
    Medium: '#FEF9C3',   // yellow-100 — caution
    Low:    '#DCFCE7',   // green-100  — safe
    Focus:  '#DBEAFE',   // blue-100   — focus country, transient empty snapshot
    None:   '#F1F5F9',   // slate-100  — not a focus country
};
const _RISK_STROKE = {
    High:   '#EF4444',   // red-500
    Medium: '#F59E0B',   // amber-500
    Low:    '#22C55E',   // green-500
    Focus:  '#3B82F6',   // blue-500 — focus-country border
    None:   '#CBD5E1',   // slate-300
};
const _BADGE_BG = {
    High:   '#DC2626',   // red-600
    Medium: '#CA8A04',   // yellow-600
    Low:    '#16A34A',   // green-600
    Focus:  '#3B82F6',   // blue-500
    None:   '#94A3B8',   // slate-400
};

// Normalise legacy "Extreme" level → "High" so old data still renders correctly
function _normalizeLevel(level) {
    return level === 'Extreme' ? 'High' : level;
}

// ── Module state
let _mapCountryData = {};
let _mapTopNews     = {};   // iso3 → { title, score } of top-scored alert
let _mapRendered    = false;
// True only when the latest /api/guardian/dashboard fetch succeeded
// AND the payload's status field was 'success'. Used to distinguish
// the "focus country with genuinely zero alerts in the snapshot"
// case from the "we couldn't load the dashboard at all" case — the
// previous code rendered both as "Tracked — no alerts", which lied
// to operators when the backend was actually down.
let _mapDataOk = false;

// ── Compute initial pan+zoom so all 8 focus countries fit in view ─
// Returns { zoom, panX, panY } given the container's W and H.
function _initialView(W, H) {
    // Mercator project helper (same formula as _drawMap)
    function proj(lon, lat) {
        const x      = (lon + 180) / 360 * W;
        const latRad = lat * Math.PI / 180;
        const mercN  = Math.log(Math.tan(Math.PI / 4 + latRad / 2));
        const y      = (H / 2) - (W * mercN / (2 * Math.PI));
        return [x, y];
    }

    // Collect projected centroids of core 8 countries (BRA/AUS/FRA excluded so
    // the initial zoom stays tight on the primary risk corridor)
    const pts = _INIT_VIEW_KEYS.map(iso3 => proj(..._CENTROIDS[iso3]));
    const xs  = pts.map(p => p[0]);
    const ys  = pts.map(p => p[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);

    // Add generous padding (countries are bigger than their centroids)
    const padX = 70, padY = 80;
    const bboxW = (maxX - minX) + padX * 2;
    const bboxH = (maxY - minY) + padY * 2;

    // Scale to fit bbox in container, capped at 2.0
    const zoom  = Math.min(2.0, Math.min(W / bboxW, H / bboxH));
    // Translate so bbox centre lands at container centre
    const cxProj = (minX + maxX) / 2;
    const cyProj = (minY + maxY) / 2;
    const panX   = W / 2 - cxProj * zoom;
    const panY   = H / 2 - cyProj * zoom;
    return { zoom, panX, panY };
}

// ── Entry point
async function loadRiskMap() {
    const container = document.getElementById('riskMapContainer');
    if (!container) return;

    // Show loading state immediately
    container.innerHTML =
        '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;' +
        'height:100%;gap:10px;color:var(--text-muted);font-size:11px;font-family:var(--font-mono);">' +
        '<div class="map-loading-spinner"></div>' +
        '<span>Loading risk map…</span>' +
        '</div>';

    try {
        const r = await fetch(`${API_BASE}/guardian/dashboard`);
        const d = await r.json();
        // `_mapDataOk` gates the downstream "no alerts" vs "unavailable"
        // tooltip text. Only flip it true on a clean success response;
        // a 200 with status='missing'/'error' is still a data-unavailable
        // state from the operator's perspective.
        _mapDataOk = d.status === 'success';
        const board = _mapDataOk
            ? (d.country_board || d.country_rank_top5 || [])
            : [];

        _mapCountryData = {};
        board.forEach(row => {
            const name = row.country || '';
            const iso3 = _ISO3[name] || _nameSearch(name);
            if (iso3) _mapCountryData[iso3] = row;
        });

        // Build top-news index: iso3 → highest-scored alert title
        _mapTopNews = {};
        (d.alerts || []).forEach(alert => {
            const name = alert.country || '';
            const iso3 = _ISO3[name] || _nameSearch(name);
            if (!iso3 || !alert.title) return;
            const score = parseFloat(alert.score) || 0;
            const existing = _mapTopNews[iso3];
            if (!existing || score > existing.score) {
                _mapTopNews[iso3] = { title: alert.title, score };
            }
        });

        await _drawMap(container);
    } catch(e) {
        console.warn('loadRiskMap error:', e);
        // Wipe stale state so a re-render after a network blip can't
        // bleed previous-snapshot numbers into the "unavailable"
        // tooltip.
        _mapDataOk = false;
        _mapCountryData = {};
        _mapTopNews = {};
        await _drawMap(container);   // still render map skeleton
    }
}

function _nameSearch(name) {
    const n = name.toLowerCase().replace(/^the\s+/, '');
    for (const [k, v] of Object.entries(_ISO3)) {
        if (k.toLowerCase().replace(/^the\s+/, '') === n) return v;
    }
    return null;
}

function _showMapEmpty(container) {
    container.innerHTML =
        '<div style="display:flex;align-items:center;justify-content:center;height:100%;' +
        'color:var(--text-muted);font-size:11px;font-family:var(--font-mono);">Map data unavailable.</div>';
}

// ── Draw SVG map ──────────────────────────────────────────────

async function _drawMap(container) {
    let topo;
    try {
        const res = await fetch('https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json');
        topo = await res.json();
    } catch(e) { _showMapEmpty(container); return; }

    const countries   = topojson.feature(topo, topo.objects.countries).features;
    const NUM_TO_ISO3 = _buildNumericIndex();

    const W = container.clientWidth  || 720;
    const H = container.clientHeight || 280;

    // Mercator projection — clamped at ±85° to avoid arithmetic overflow near poles
    function project(lon, lat) {
        lat = Math.max(-85, Math.min(85, lat));
        const x      = (lon + 180) / 360 * W;
        const latRad = lat * Math.PI / 180;
        const mercN  = Math.log(Math.tan(Math.PI / 4 + latRad / 2));
        const y      = (H / 2) - (W * mercN / (2 * Math.PI));
        return [x, y];
    }

    // Normalize ring longitudes to prevent antimeridian wrap-around artifacts.
    // Canada has territory near ±180°; without this, consecutive vertices can jump
    // ~360° in x, causing SVG's fill engine to draw a horizontal band across the map.
    function _normalizeRing(coords) {
        const out = [coords[0].slice()];
        for (let i = 1; i < coords.length; i++) {
            let lon = coords[i][0];
            const prev = out[i - 1][0];
            while (lon - prev >  180) lon -= 360;
            while (lon - prev < -180) lon += 360;
            out.push([lon, coords[i][1]]);
        }
        return out;
    }

    // Skip rings whose southernmost projected point is still above canvas y = -(H/4).
    // In Mercator (with 85° clamp) this corresponds to ~72°N — removes extreme arctic
    // island polygons that would flood the viewport as large fills when panning.
    function ringToPath(coords) {
        const norm = _normalizeRing(coords);
        const pts  = norm.map(pt => project(pt[0], pt[1]));
        const maxY = Math.max(...pts.map(([, y]) => y));
        if (maxY < -(H / 4)) return '';
        return pts.map(([x, y], i) =>
            (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1)
        ).join(' ') + ' Z';
    }
    function geometryToPath(geom) {
        if (!geom) return '';
        if (geom.type === 'Polygon')
            return geom.coordinates.map(ringToPath).filter(Boolean).join(' ');
        if (geom.type === 'MultiPolygon')
            return geom.coordinates.map(p => p.map(ringToPath).filter(Boolean).join(' ')).join(' ');
        return '';
    }

    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('width',    '100%');
    svg.setAttribute('height',   '100%');
    svg.setAttribute('viewBox',  `0 0 ${W} ${H}`);
    svg.style.display = 'block';
    svg.style.cursor  = 'grab';

    // Ocean background — sits at root level so it fills the viewBox regardless of pan.
    // Container background (#EFF6FF) covers any gap when panning beyond the rect edge.
    const bg = document.createElementNS(ns, 'rect');
    bg.setAttribute('x', -W); bg.setAttribute('y', -H);
    bg.setAttribute('width', W * 3); bg.setAttribute('height', H * 3);
    bg.setAttribute('fill', '#EFF6FF');
    svg.appendChild(bg);

    // Single draggable canvas wrapping all map content
    const canvas = document.createElementNS(ns, 'g');
    canvas.setAttribute('id', 'mapCanvas');
    svg.appendChild(canvas);

    // Layers inside canvas
    const pathGroup  = document.createElementNS(ns, 'g');
    const labelGroup = document.createElementNS(ns, 'g');
    const badgeGroup = document.createElementNS(ns, 'g');
    canvas.appendChild(pathGroup);
    canvas.appendChild(labelGroup);
    canvas.appendChild(badgeGroup);

    // Tooltip
    const tooltip = document.createElement('div');
    tooltip.className     = 'map-tooltip';
    tooltip.style.display = 'none';
    container.style.position = 'relative';

    // ── Pan + zoom state ──────────────────────────────────────
    const _init = _initialView(W, H);
    let _pan    = { x: _init.panX, y: _init.panY };
    let _zoom   = _init.zoom;
    let _drag   = null;   // { startX, startY, panX, panY } while dragging
    let _moved  = false;  // true once pointer moved enough to count as a drag

    function _applyTransform() {
        canvas.setAttribute('transform', `translate(${_pan.x},${_pan.y}) scale(${_zoom})`);
    }
    _applyTransform();

    function _onPointerDown(e) {
        if (e.button !== undefined && e.button !== 0) return;
        _drag  = { startX: e.clientX, startY: e.clientY, panX: _pan.x, panY: _pan.y };
        _moved = false;
        svg.style.cursor = 'grabbing';
        e.preventDefault();
    }
    function _onPointerMove(e) {
        if (!_drag) return;
        const dx = e.clientX - _drag.startX;
        const dy = e.clientY - _drag.startY;
        if (!_moved && Math.abs(dx) + Math.abs(dy) > 3) _moved = true;
        if (_moved) {
            tooltip.style.display = 'none';
            _pan.x = _drag.panX + dx;
            _pan.y = _drag.panY + dy;
            _applyTransform();
        }
    }
    function _onPointerUp() {
        _drag = null;
        svg.style.cursor = 'grab';
    }

    // Touch equivalents
    function _onTouchStart(e) {
        const t = e.touches[0];
        _drag  = { startX: t.clientX, startY: t.clientY, panX: _pan.x, panY: _pan.y };
        _moved = false;
    }
    function _onTouchMove(e) {
        if (!_drag) return;
        const t  = e.touches[0];
        const dx = t.clientX - _drag.startX;
        const dy = t.clientY - _drag.startY;
        if (!_moved && Math.abs(dx) + Math.abs(dy) > 3) _moved = true;
        if (_moved) { _pan.x = _drag.panX + dx; _pan.y = _drag.panY + dy; _applyTransform(); e.preventDefault(); }
    }
    function _onTouchEnd() { _drag = null; }

    svg.addEventListener('mousedown',  _onPointerDown);
    window.addEventListener('mousemove', _onPointerMove);
    window.addEventListener('mouseup',   _onPointerUp);
    svg.addEventListener('touchstart', _onTouchStart, { passive: true });
    svg.addEventListener('touchmove',  _onTouchMove,  { passive: false });
    svg.addEventListener('touchend',   _onTouchEnd);

    // Double-click resets to initial focus-countries view
    svg.addEventListener('dblclick', () => {
        _pan  = { x: _init.panX, y: _init.panY };
        _zoom = _init.zoom;
        _applyTransform();
    });

    // ── Draw country paths ────────────────────────────────────
    countries.forEach(feature => {
        const numId  = String(feature.id);
        const iso3   = NUM_TO_ISO3[numId];
        const data   = iso3 ? _mapCountryData[iso3] : null;
        const isFocus = iso3 && (iso3 in _FOCUS);
        const level  = data ? _normalizeLevel(data.risk_level || 'None')
                             : isFocus ? 'Focus' : 'None';

        const fill   = _RISK_FILL[level]   || _RISK_FILL.None;
        const stroke = isFocus && !data
            ? _RISK_STROKE.Focus
            : (_RISK_STROKE[level] || _RISK_STROKE.None);
        const sw = isFocus ? (data ? '1.2' : '1.0') : (data ? '0.6' : '0.3');

        const path = document.createElementNS(ns, 'path');
        path.setAttribute('d',            geometryToPath(feature.geometry));
        path.setAttribute('fill',         fill);
        path.setAttribute('fill-rule',    'evenodd');
        path.setAttribute('stroke',       stroke);
        path.setAttribute('stroke-width', sw);
        path.style.transition = 'filter 0.15s';
        path.style.cursor     = (data || isFocus) ? 'pointer' : 'default';

        const tooltipData = data || (isFocus ? { country: _FOCUS[iso3], alert_count: 0, risk_level: 'Tracked', top_category: 'Focus country' } : null);
        if (tooltipData) {
            const clickName = data ? data.country : (isFocus ? _FOCUS[iso3] : null);
            path.addEventListener('mouseenter', () => {
                if (_moved) return;
                path.style.filter = 'brightness(0.88)';
                tooltip.style.display = 'block';
                const topNews = _mapTopNews[iso3];
                // Three states for the no-data fallback line:
                //   `data` present                → render real metrics
                //   no `data`, but `_mapDataOk`   → genuinely 0 alerts today
                //   no `data` AND `!_mapDataOk`   → dashboard fetch failed,
                //                                   don't pretend it's zero
                const fallbackText = _mapDataOk
                    ? 'Tracked — no alerts in current snapshot'
                    : 'Data unavailable — dashboard fetch failed';
                tooltip.innerHTML =
                    `<strong>${tooltipData.country}</strong><br>` +
                    (data
                        ? `Risk: <b>${_normalizeLevel(data.risk_level)}</b> · ${data.alert_count} alert${data.alert_count !== 1 ? 's' : ''}<br>` +
                          `<span style="font-size:9px;color:#64748B;">Top news: ${topNews ? topNews.title : (data.top_category || '—')}</span>`
                        : `<span style="font-size:9px;color:#3B82F6;">${fallbackText}</span>`
                    );
            });
            path.addEventListener('mousemove', e => { if (!_moved) _positionTooltip(e, tooltip, container); });
            path.addEventListener('mouseleave', () => { path.style.filter = ''; tooltip.style.display = 'none'; });
            if (clickName) {
                path.addEventListener('click', () => {
                    if (_moved) return;
                    tooltip.style.display = 'none';
                    showCountryEvents(clickName);
                });
                path.style.cursor = 'pointer';
            }
        }
        pathGroup.appendChild(path);
    });

    // ── Draw focus-country name labels ────────────────────────
    Object.entries(_FOCUS).forEach(([iso3, label]) => {
        const centroid = _CENTROIDS[iso3];
        if (!centroid) return;
        const [cx, cy] = project(centroid[0], centroid[1]);

        const data   = _mapCountryData[iso3];

        // All focus countries now have a badge — label always sits below it
        const labelY = cy + 22;

        // Background pill for readability
        const pillW = label.length * 5.5 + 8;
        const pill  = document.createElementNS(ns, 'rect');
        pill.setAttribute('x',      (cx - pillW / 2).toFixed(1));
        pill.setAttribute('y',      (labelY - 8).toFixed(1));
        pill.setAttribute('width',  pillW.toFixed(1));
        pill.setAttribute('height', '11');
        pill.setAttribute('rx',     '3');
        pill.setAttribute('fill',   data ? (_BADGE_BG[data.risk_level] || '#64748B') : '#3B82F6');
        pill.setAttribute('opacity','0.85');
        pill.setAttribute('pointer-events', 'none');

        const txt = document.createElementNS(ns, 'text');
        txt.setAttribute('x',           cx.toFixed(1));
        txt.setAttribute('y',           labelY.toFixed(1));
        txt.setAttribute('text-anchor', 'middle');
        txt.setAttribute('font-size',   '7');
        txt.setAttribute('font-weight', '700');
        txt.setAttribute('fill',        'white');
        txt.setAttribute('font-family', 'IBM Plex Mono, monospace');
        txt.setAttribute('pointer-events', 'none');
        txt.textContent = label;

        labelGroup.appendChild(pill);
        labelGroup.appendChild(txt);
    });

    // ── Draw alert-count badges for countries with data ───────
    Object.entries(_mapCountryData).forEach(([iso3, data]) => {
        const centroid = _CENTROIDS[iso3];
        if (!centroid) return;
        const [cx, cy] = project(centroid[0], centroid[1]);

        const level  = _normalizeLevel(data.risk_level || 'None');
        const count  = data.alert_count || 0;
        const bgCol  = _BADGE_BG[level] || _BADGE_BG.None;
        const label  = count > 99 ? '99+' : String(count);
        const r      = label.length >= 3 ? 11 : 9;

        const g = document.createElementNS(ns, 'g');
        g.style.cursor = 'pointer';

        const circle = document.createElementNS(ns, 'circle');
        circle.setAttribute('cx',           cx.toFixed(1));
        circle.setAttribute('cy',           cy.toFixed(1));
        circle.setAttribute('r',            r);
        circle.setAttribute('fill',         bgCol);
        circle.setAttribute('stroke',       'white');
        circle.setAttribute('stroke-width', '1.5');

        // Pulse ring for High-risk countries
        if (level === 'High') {
            const pulse = document.createElementNS(ns, 'circle');
            pulse.setAttribute('cx',   cx.toFixed(1));
            pulse.setAttribute('cy',   cy.toFixed(1));
            pulse.setAttribute('r',    r + 3);
            pulse.setAttribute('fill', 'none');
            pulse.setAttribute('stroke', bgCol);
            pulse.setAttribute('stroke-width', '1');
            pulse.setAttribute('opacity', '0.4');
            pulse.setAttribute('pointer-events', 'none');
            g.appendChild(pulse);
        }

        const text = document.createElementNS(ns, 'text');
        text.setAttribute('x',              cx.toFixed(1));
        text.setAttribute('y',              (cy + 3.5).toFixed(1));
        text.setAttribute('text-anchor',    'middle');
        text.setAttribute('font-size',      label.length >= 3 ? '7' : '8');
        text.setAttribute('font-weight',    '700');
        text.setAttribute('fill',           'white');
        text.setAttribute('font-family',    'IBM Plex Mono, monospace');
        text.setAttribute('pointer-events', 'none');
        text.textContent = label;

        g.appendChild(circle);
        g.appendChild(text);

        g.addEventListener('mouseenter', () => {
            if (_moved) return;
            circle.setAttribute('r', r + 2);
            tooltip.style.display = 'block';
            const topNews = _mapTopNews[iso3];
            tooltip.innerHTML =
                `<strong>${data.country}</strong><br>` +
                `Risk: <b>${_normalizeLevel(data.risk_level)}</b> · ${data.alert_count} alert${data.alert_count !== 1 ? 's' : ''}<br>` +
                `<span style="font-size:9px;color:#64748B;">Top news: ${topNews ? topNews.title : (data.top_category || '—')}</span>`;
        });
        g.addEventListener('mousemove', e => { if (!_moved) _positionTooltip(e, tooltip, container); });
        g.addEventListener('mouseleave', () => { circle.setAttribute('r', r); tooltip.style.display = 'none'; });
        g.addEventListener('click', () => {
            if (_moved) return;
            tooltip.style.display = 'none';
            showCountryEvents(data.country);
        });

        badgeGroup.appendChild(g);
    });

    // ── Draw "0" badges for focus countries with no alerts in the
    //    current snapshot. All five _FOCUS entries have a native
    //    source upstream, so this branch is for transient empties
    //    (slow news day) — not for missing-coverage cases.
    Object.entries(_FOCUS).forEach(([iso3, displayName]) => {
        if (_mapCountryData[iso3]) return;   // already has a real badge above
        const centroid = _CENTROIDS[iso3];
        if (!centroid) return;
        const [cx, cy] = project(centroid[0], centroid[1]);

        const r = 9;
        const g = document.createElementNS(ns, 'g');
        g.style.cursor = 'pointer';

        const circle = document.createElementNS(ns, 'circle');
        circle.setAttribute('cx',           cx.toFixed(1));
        circle.setAttribute('cy',           cy.toFixed(1));
        circle.setAttribute('r',            r);
        circle.setAttribute('fill',         '#3B82F6');
        circle.setAttribute('stroke',       'white');
        circle.setAttribute('stroke-width', '1.5');
        circle.setAttribute('opacity',      '0.75');

        const text = document.createElementNS(ns, 'text');
        text.setAttribute('x',              cx.toFixed(1));
        text.setAttribute('y',              (cy + 3.5).toFixed(1));
        text.setAttribute('text-anchor',    'middle');
        text.setAttribute('font-size',      '8');
        text.setAttribute('font-weight',    '700');
        text.setAttribute('fill',           'white');
        text.setAttribute('font-family',    'IBM Plex Mono, monospace');
        text.setAttribute('pointer-events', 'none');
        // Numeric "0" vs dash "—": same three-state logic as the
        // country-path tooltip. If we couldn't load the dashboard at
        // all, rendering "0" implies certainty we don't have.
        text.textContent = _mapDataOk ? '0' : '—';

        g.appendChild(circle);
        g.appendChild(text);

        g.addEventListener('mouseenter', () => {
            if (_moved) return;
            circle.setAttribute('r', r + 2);
            tooltip.style.display = 'block';
            const badgeText = _mapDataOk
                ? 'Tracked — no alerts in current snapshot'
                : 'Data unavailable — dashboard fetch failed';
            tooltip.innerHTML =
                `<strong>${displayName}</strong><br>` +
                `<span style="font-size:9px;color:#3B82F6;">${badgeText}</span>`;
        });
        g.addEventListener('mousemove', e => { if (!_moved) _positionTooltip(e, tooltip, container); });
        g.addEventListener('mouseleave', () => { circle.setAttribute('r', r); tooltip.style.display = 'none'; });
        g.addEventListener('click', () => {
            if (_moved) return;
            tooltip.style.display = 'none';
            showCountryEvents(displayName);
        });

        badgeGroup.appendChild(g);
    });

    // Clean up window listeners if map is ever re-rendered
    if (container._mapCleanup) container._mapCleanup();
    container._mapCleanup = () => {
        window.removeEventListener('mousemove', _onPointerMove);
        window.removeEventListener('mouseup',   _onPointerUp);
    };

    container.innerHTML = '';
    container.appendChild(svg);
    container.appendChild(tooltip);

    const hint = document.createElement('div');
    hint.className   = 'risk-map-hint';
    hint.textContent = 'click country → events · drag to pan · double-click to reset';
    container.appendChild(hint);

    _mapRendered = true;
}

function _positionTooltip(e, tooltip, container) {
    const rect = container.getBoundingClientRect();
    let tx = e.clientX - rect.left + 12;
    let ty = e.clientY - rect.top  - 12;
    if (tx + 170 > rect.width)  tx -= 180;
    if (ty + 70  > rect.height) ty -= 75;
    tooltip.style.left = tx + 'px';
    tooltip.style.top  = ty + 'px';
}

// ── ISO-3166-1 numeric → alpha-3 ─────────────────────────────
function _buildNumericIndex() {
    return {
        '4':'AFG','8':'ALB','12':'DZA','24':'AGO','32':'ARG','36':'AUS',
        '40':'AUT','50':'BGD','56':'BEL','68':'BOL','76':'BRA','100':'BGR',
        '116':'KHM','120':'CMR','124':'CAN','152':'CHL','156':'CHN',
        '170':'COL','180':'COD','191':'HRV','192':'CUB','203':'CZE',
        '208':'DNK','218':'ECU','818':'EGY','231':'ETH','246':'FIN',
        '250':'FRA','276':'DEU','288':'GHA','300':'GRC','320':'GTM',
        '348':'HUN','356':'IND','360':'IDN','364':'IRN','368':'IRQ',
        '372':'IRL','376':'ISR','380':'ITA','392':'JPN','400':'JOR',
        '398':'KAZ','404':'KEN','408':'PRK','410':'KOR','414':'KWT',
        '422':'LBN','434':'LBY','458':'MYS','484':'MEX','504':'MAR',
        '508':'MOZ','104':'MMR','528':'NLD','554':'NZL','566':'NGA',
        '578':'NOR','586':'PAK','604':'PER','608':'PHL','616':'POL',
        '620':'PRT','642':'ROU','643':'RUS','682':'SAU','688':'SRB',
        '706':'SOM','710':'ZAF','724':'ESP','144':'LKA','729':'SDN',
        '752':'SWE','756':'CHE','760':'SYR','764':'THA','792':'TUR',
        '804':'UKR','784':'ARE','826':'GBR','840':'USA','862':'VEN',
        '704':'VNM','887':'YEM','716':'ZWE','702':'SGP','158':'TWN',
        '44':'BHS','31':'AZE','51':'ARM','860':'UZB','762':'TJK',
        '795':'TKM','417':'KGZ','496':'MNG',
    };
}
