/* Interactive map (Leaflet + OpenStreetMap tiles, needs internet for the tiles only). Numbered markers, optional route line, popups.
   Without Leaflet, or when the tiles can't load, the places are still shown as a plain scatter plot with names, so a map is never empty. */
const MapView = (() => {
  const GROUP_COLORS = ['#d9534f', '#2e86de', '#27ae60', '#f39c12', '#8e44ad', '#16a085', '#e84393', '#576574'];
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function colorFor(spec) {
    const groups = [...new Set(spec.points.map((p) => p.group || ''))];
    return (p) => GROUP_COLORS[groups.indexOf(p.group || '') % GROUP_COLORS.length];
  }

  function legendHTML(spec, color) {
    const groups = [...new Set(spec.points.map((p) => p.group).filter(Boolean))];
    if (!groups.length) return '';
    return '<div class="map-legend">' + groups.map((g) => `<span><i style="background:${color({ group: g })}"></i>${esc(g)}</span>`).join('') + '</div>';
  }

  /** Plain SVG fallback: equirectangular scatter of the places, with the route as a line. */
  function fallbackSVG(spec, color) {
    const lats = spec.points.map((p) => p.lat), lons = spec.points.map((p) => p.lon);
    const minLa = Math.min(...lats), maxLa = Math.max(...lats), minLo = Math.min(...lons), maxLo = Math.max(...lons);
    const W = 640, H = 360, pad = 40;
    const sx = (lo) => pad + ((lo - minLo) / Math.max(maxLo - minLo, 0.01)) * (W - 2 * pad);
    const sy = (la) => H - pad - ((la - minLa) / Math.max(maxLa - minLa, 0.01)) * (H - 2 * pad);
    const line = spec.route && spec.points.length > 1 ? `<polyline fill="none" stroke="#888" stroke-width="2" stroke-dasharray="5 4" points="${spec.points.map((p) => `${sx(p.lon)},${sy(p.lat)}`).join(' ')}"/>` : '';
    const dots = spec.points.map((p) => `<g><circle cx="${sx(p.lon)}" cy="${sy(p.lat)}" r="11" fill="${color(p)}"/><text x="${sx(p.lon)}" y="${sy(p.lat) + 4}" text-anchor="middle" fill="#fff" font-size="11" font-weight="700">${p.n}</text><text x="${sx(p.lon) + 15}" y="${sy(p.lat) + 4}" font-size="11" fill="currentColor">${esc(p.name)}</text></g>`).join('');
    return `<svg viewBox="0 0 ${W} ${H}" class="map-fallback" role="img" aria-label="Places (offline view)">${line}${dots}</svg>`;
  }

  function render(holder, spec) {
    if (!spec || !Array.isArray(spec.points) || !spec.points.length) { holder.textContent = 'No places to show.'; return; }
    const color = colorFor(spec);
    holder.className = 'map-block';
    const dist = spec.distance ? `<span class="map-dist" title="${spec.distance.method === 'osrm' ? 'Driving distance from OpenStreetMap routing' : 'Straight-line estimate x1.3; routing service unreachable'}">${spec.distance.km} km · ${Math.floor(spec.distance.minutes / 60)}h ${spec.distance.minutes % 60}m${spec.distance.method === 'osrm' ? '' : ' (estimate)'}</span>` : '';
    const first = spec.points[0];
    holder.innerHTML =
      `<div class="map-head"><span class="map-title">${esc(spec.title)}</span>${dist}` +
      `<a class="map-open" target="_blank" rel="noopener noreferrer" href="https://www.openstreetmap.org/?mlat=${first.lat}&mlon=${first.lon}#map=11/${first.lat}/${first.lon}">Open in OpenStreetMap</a></div>` +
      `<div class="map-canvas"></div>${legendHTML(spec, color)}` +
      (spec.skipped && spec.skipped.length ? `<div class="map-skipped muted small">Not located: ${spec.skipped.map(esc).join(', ')}</div>` : '') +
      `<div class="map-attr muted small">Map data © OpenStreetMap contributors</div>`;
    const el = holder.querySelector('.map-canvas');
    if (typeof L === 'undefined') { el.innerHTML = fallbackSVG(spec, color); return; }
    const map = L.map(el, { scrollWheelZoom: false, attributionControl: false });
    let tilesOk = false;
    const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18, crossOrigin: true });
    tiles.on('tileload', () => { tilesOk = true; });
    tiles.on('tileerror', () => { if (!tilesOk && !holder.querySelector('.map-offline')) { const n = document.createElement('div'); n.className = 'map-offline muted small'; n.textContent = 'Map tiles could not load (offline?). Places are still shown.'; holder.querySelector('.map-attr').before(n); } });
    tiles.addTo(map);
    const latlngs = [];
    for (const p of spec.points) {
      const icon = L.divIcon({ className: 'map-pin', html: `<span style="background:${color(p)}">${p.n}</span>`, iconSize: [26, 26], iconAnchor: [13, 13] });
      L.marker([p.lat, p.lon], { icon }).addTo(map).bindPopup(`<b>${esc(p.name)}</b>${p.group ? `<br><i>${esc(p.group)}</i>` : ''}${p.note ? `<br>${esc(p.note)}` : ''}${p.value != null ? `<br>${esc(p.value)}` : ''}`);
      latlngs.push([p.lat, p.lon]);
    }
    if (spec.route && latlngs.length > 1) L.polyline(latlngs, { color: '#2e86de', weight: 3, opacity: 0.8, dashArray: '6 6' }).addTo(map);
    if (latlngs.length === 1) map.setView(latlngs[0], 12); else map.fitBounds(latlngs, { padding: [30, 30], maxZoom: 14 });
    holder._map = map;
    new IntersectionObserver((es, o) => { if (es[0].isIntersecting) { map.invalidateSize(); o.disconnect(); } }).observe(el); // sized correctly once visible
    el.addEventListener('click', () => map.scrollWheelZoom.enable(), { once: true }); // wheel zoom only after a click, so the page still scrolls
  }

  return { render };
})();
