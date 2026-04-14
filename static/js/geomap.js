/* ============================================================
   ARGUS — Geospatial Risk Map
   Renders an SVG world map on the home page with:
     • Country fill colour keyed to risk level
     • 8 focus countries always highlighted with border + label
     • Circular badge showing alert count per country
   Uses TopoJSON world-110m (loaded from CDN) + a minimal
   Mercator projection implemented without D3.
   ============================================================ */

// ── 8 always-highlighted focus countries (ISO-3 → display label)
const _FOCUS = {
    USA: 'US',
    CHN: 'China',
    CAN: 'Canada',
    GBR: 'UK',
    JPN: 'Japan',
    MEX: 'Mexico',
    IND: 'India',
    KOR: 'Korea',
};

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

// ── Risk colour palette
const _RISK_FILL = {
    Extreme:'#FCA5A5', High:'#FCD34D', Medium:'#86EFAC', Low:'#BAE6FD',
    // Focus-country tint when no risk data
    Focus:  '#DBEAFE',   // blue-100
    None:   '#E2E8F0',
};
const _RISK_STROKE = {
    Extreme:'#EF4444', High:'#F59E0B', Medium:'#22C55E', Low:'#38BDF8',
    Focus:  '#3B82F6',   // blue-500 — focus border
    None:   '#CBD5E1',
};
const _BADGE_BG = {
    Extreme:'#EF4444', High:'#F59E0B', Medium:'#16A34A', Low:'#0284C7',
    Focus:  '#3B82F6', None:'#94A3B8',
};

// ── Module state
let _mapCountryData = {};
let _mapRendered    = false;

// ── Entry point
async function loadRiskMap() {
    const container = document.getElementById('riskMapContainer');
    if (!container) return;

    try {
        const r = await fetch(`${API_BASE}/guardian/dashboard`);
        const d = await r.json();
        // Accept missing data — we still render focus countries
        const board = (d.status === 'success')
            ? (d.country_board || d.country_rank_top5 || [])
            : [];

        _mapCountryData = {};
        board.forEach(row => {
            const name = row.country || '';
            const iso3 = _ISO3[name] || _nameSearch(name);
            if (iso3) _mapCountryData[iso3] = row;
        });

        await _drawMap(container);
    } catch(e) {
        console.warn('loadRiskMap error:', e);
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

    // Mercator projection
    function project(lon, lat) {
        const x      = (lon + 180) / 360 * W;
        const latRad = lat * Math.PI / 180;
        const mercN  = Math.log(Math.tan(Math.PI / 4 + latRad / 2));
        const y      = (H / 2) - (W * mercN / (2 * Math.PI));
        return [x, y];
    }

    function ringToPath(coords) {
        return coords.map((pt, i) => {
            const [x, y] = project(pt[0], pt[1]);
            return (i === 0 ? 'M' : 'L') + x.toFixed(1) + ',' + y.toFixed(1);
        }).join(' ') + ' Z';
    }
    function geometryToPath(geom) {
        if (!geom) return '';
        if (geom.type === 'Polygon')      return geom.coordinates.map(ringToPath).join(' ');
        if (geom.type === 'MultiPolygon') return geom.coordinates.map(p => p.map(ringToPath).join(' ')).join(' ');
        return '';
    }

    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('width',   '100%');
    svg.setAttribute('height',  '100%');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.style.display = 'block';

    // Ocean
    const bg = document.createElementNS(ns, 'rect');
    bg.setAttribute('width', W); bg.setAttribute('height', H); bg.setAttribute('fill', '#EFF6FF');
    svg.appendChild(bg);

    // Layers
    const pathGroup  = document.createElementNS(ns, 'g');
    const labelGroup = document.createElementNS(ns, 'g');  // country name labels
    const badgeGroup = document.createElementNS(ns, 'g');  // alert-count badges
    svg.appendChild(pathGroup);
    svg.appendChild(labelGroup);
    svg.appendChild(badgeGroup);

    // Tooltip
    const tooltip = document.createElement('div');
    tooltip.className    = 'map-tooltip';
    tooltip.style.display = 'none';
    container.style.position = 'relative';

    // ── Draw country paths ────────────────────────────────────
    countries.forEach(feature => {
        const numId  = String(feature.id);
        const iso3   = NUM_TO_ISO3[numId];
        const data   = iso3 ? _mapCountryData[iso3] : null;
        const isFocus = iso3 && (iso3 in _FOCUS);
        const level  = data ? (data.risk_level || 'None')
                             : isFocus ? 'Focus' : 'None';

        const fill   = _RISK_FILL[level]   || _RISK_FILL.None;
        const stroke = isFocus && !data
            ? _RISK_STROKE.Focus
            : (_RISK_STROKE[level] || _RISK_STROKE.None);
        const sw = isFocus ? (data ? '1.2' : '1.0') : (data ? '0.6' : '0.3');

        const path = document.createElementNS(ns, 'path');
        path.setAttribute('d',            geometryToPath(feature.geometry));
        path.setAttribute('fill',         fill);
        path.setAttribute('stroke',       stroke);
        path.setAttribute('stroke-width', sw);
        path.style.transition = 'filter 0.15s';
        path.style.cursor     = (data || isFocus) ? 'pointer' : 'default';

        const tooltipData = data || (isFocus ? { country: _FOCUS[iso3], alert_count: 0, risk_level: 'Monitored', top_category: 'Focus country' } : null);
        if (tooltipData) {
            path.addEventListener('mouseenter', () => {
                path.style.filter = 'brightness(0.88)';
                tooltip.style.display = 'block';
                tooltip.innerHTML =
                    `<strong>${tooltipData.country}</strong><br>` +
                    (data
                        ? `${data.alert_count} alert${data.alert_count !== 1 ? 's' : ''} · ${data.risk_level}<br>` +
                          `<span style="font-size:9px;color:#94A3B8;">${data.top_category || ''}</span>`
                        : `<span style="font-size:9px;color:#3B82F6;">Focus country — no active alerts</span>`
                    );
            });
            path.addEventListener('mousemove', e => _positionTooltip(e, tooltip, container));
            path.addEventListener('mouseleave', () => { path.style.filter = ''; tooltip.style.display = 'none'; });
        }
        pathGroup.appendChild(path);
    });

    // ── Draw focus-country name labels ────────────────────────
    Object.entries(_FOCUS).forEach(([iso3, label]) => {
        const centroid = _CENTROIDS[iso3];
        if (!centroid) return;
        const [cx, cy] = project(centroid[0], centroid[1]);
        if (cx < 4 || cy < 4 || cx > W - 4 || cy > H - 4) return;

        const data   = _mapCountryData[iso3];
        const hasData = !!data;

        // Offset label below the badge (or just below centroid if no badge)
        const labelY = cy + (hasData ? 22 : 13);

        // Background pill for readability
        const pillW = label.length * 5.5 + 8;
        const pill  = document.createElementNS(ns, 'rect');
        pill.setAttribute('x',      (cx - pillW / 2).toFixed(1));
        pill.setAttribute('y',      (labelY - 8).toFixed(1));
        pill.setAttribute('width',  pillW.toFixed(1));
        pill.setAttribute('height', '11');
        pill.setAttribute('rx',     '3');
        pill.setAttribute('fill',   hasData ? (_BADGE_BG[data.risk_level] || '#64748B') : '#3B82F6');
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
        if (cx < 4 || cy < 4 || cx > W - 4 || cy > H - 4) return;

        const level  = data.risk_level || 'None';
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

        // Pulse ring for Extreme/High
        if (level === 'Extreme' || level === 'High') {
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
            circle.setAttribute('r', r + 2);
            tooltip.style.display = 'block';
            tooltip.innerHTML =
                `<strong>${data.country}</strong><br>` +
                `${data.alert_count} alert${data.alert_count !== 1 ? 's' : ''} · ${data.risk_level}<br>` +
                `<span style="font-size:9px;color:#94A3B8;">${data.top_category || ''}</span>`;
        });
        g.addEventListener('mousemove', e => _positionTooltip(e, tooltip, container));
        g.addEventListener('mouseleave', () => { circle.setAttribute('r', r); tooltip.style.display = 'none'; });

        badgeGroup.appendChild(g);
    });

    container.innerHTML = '';
    container.appendChild(svg);
    container.appendChild(tooltip);
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
