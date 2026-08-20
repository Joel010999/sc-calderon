'use strict';

document.addEventListener('DOMContentLoaded', () => {

  /* ═══════════════════════════════════════════════════
     1. MOCK DATA
     ═══════════════════════════════════════════════════ */
  const TRIPS = {
    'Jujuy-Córdoba': [
      {
        id: 'jc-1',
        date: '2026-08-20',
        departureTime: '18:00',
        arrivalTime: '06:00',
        duration: '12 h',
        originPoint: 'Casa de Turismo de Jujuy',
        destinationPoint: 'Terminal de Córdoba',
        price: 54900,
        availability: '8 asientos',
        service: ['Wi-Fi', 'Starlink', 'Servicio directo'],
        note: 'Conectividad durante todo el viaje.'
      },
      {
        id: 'jc-2',
        date: '2026-08-20',
        departureTime: '21:00',
        arrivalTime: '09:30',
        duration: '12 h 30 min',
        originPoint: 'Casa de Turismo de Jujuy',
        destinationPoint: 'Terminal de Córdoba',
        price: 56800,
        availability: '4 asientos',
        service: ['Wi-Fi', 'Starlink', 'Nocturno'],
        note: 'Salida nocturna con llegada por la mañana.'
      },
      {
        id: 'jc-3',
        date: '2026-08-21',
        departureTime: '18:30',
        arrivalTime: '07:00',
        duration: '12 h 30 min',
        originPoint: 'Casa de Turismo de Jujuy',
        destinationPoint: 'Terminal de Córdoba',
        price: 54900,
        availability: '12 asientos',
        service: ['Wi-Fi', 'Starlink', 'Tarifa base'],
        note: 'Tarifa de referencia basada en material comercial.'
      }
    ],
    'Córdoba-Jujuy': [
      {
        id: 'cj-1',
        date: '2026-08-20',
        departureTime: '20:00',
        arrivalTime: '08:30',
        duration: '12 h 30 min',
        originPoint: 'Terminal de Córdoba',
        destinationPoint: 'Casa de Turismo de Jujuy',
        price: 54900,
        availability: '9 asientos',
        service: ['Wi-Fi', 'Starlink', 'Servicio directo'],
        note: 'Recorrido directo con conectividad a bordo.'
      },
      {
        id: 'cj-2',
        date: '2026-08-21',
        departureTime: '18:00',
        arrivalTime: '06:30',
        duration: '12 h 30 min',
        originPoint: 'Terminal de Córdoba',
        destinationPoint: 'Casa de Turismo de Jujuy',
        price: 56200,
        availability: '6 asientos',
        service: ['Wi-Fi', 'Starlink', 'Frecuencia habitual'],
        note: 'Servicio regular con conectividad Starlink.'
      },
      {
        id: 'cj-3',
        date: '2026-08-22',
        departureTime: '21:00',
        arrivalTime: '09:30',
        duration: '12 h 30 min',
        originPoint: 'Terminal de Córdoba',
        destinationPoint: 'Casa de Turismo de Jujuy',
        price: 57600,
        availability: '3 asientos',
        service: ['Wi-Fi', 'Starlink', 'Cupos limitados'],
        note: 'Últimos lugares disponibles.'
      }
    ]
  };

  /* ═══════════════════════════════════════════════════
     2. STATE
     ═══════════════════════════════════════════════════ */
  const state = {
    origin: 'Jujuy',
    destination: 'Córdoba',
    date: '2026-08-20',
    passengers: 1,
    selectedTrip: null,
    customer: null,
    extraPassengers: 0
  };

  /* ═══════════════════════════════════════════════════
     3. DOM REFERENCES
     ═══════════════════════════════════════════════════ */
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => [...document.querySelectorAll(sel)];

  const form            = $('#search-form');
  const originSelect    = $('#origin');
  const destSelect      = $('#destination');
  const dateInput       = $('#travel-date');
  const passSelect      = $('#passengers');
  const resultsTitle    = $('#results-title');
  const resultsList     = $('#results-list');
  const searchSummary   = $('#search-summary');
  const detailCard      = $('#detail-card');
  const passengerForm   = $('#passenger-form');
  const passCountLabel  = $('#passenger-count-label');
  const addPassBtn      = $('#add-passenger');
  const extraPassEl     = $('#extra-passengers');
  const confirmSummary  = $('#confirmation-summary');
  const waLink          = $('#whatsapp-link');
  const menuToggle      = $('.menu-toggle');
  const siteNav         = $('.site-nav');
  const tabs            = $$('.screen-tab');
  const screens         = $$('.flow-screen');

  /* ═══════════════════════════════════════════════════
     4. UTILITIES
     ═══════════════════════════════════════════════════ */
  function formatDate(dateStr) {
    const d = new Date(`${dateStr}T12:00:00`);
    return new Intl.DateTimeFormat('es-AR', {
      weekday: 'long',
      day: 'numeric',
      month: 'long',
      year: 'numeric'
    }).format(d);
  }

  function formatPrice(num) {
    return new Intl.NumberFormat('es-AR').format(num);
  }

  function routeKey() {
    return `${state.origin}-${state.destination}`;
  }

  function routeLabel() {
    return `${state.origin} → ${state.destination}`;
  }

  function getVisibleTrips() {
    const all = TRIPS[routeKey()] || [];
    const exact = all.filter(t => t.date === state.date);
    return exact.length > 0 ? exact : all.slice(0, 3);
  }

  function passengerWord(n) {
    return n === 1 ? 'pasajero' : 'pasajeros';
  }

  /* ═══════════════════════════════════════════════════
     5. SERVICE PILLS
     ═══════════════════════════════════════════════════ */
  function pillsHTML(list) {
    return list.map((item, i) => {
      const accent = i === 1 ? ' service-pill--accent' : '';
      return `<span class="service-pill${accent}">${item}</span>`;
    }).join('');
  }

  /* ═══════════════════════════════════════════════════
     6. RENDER — RESULTS
     ═══════════════════════════════════════════════════ */
  function renderResults() {
    if (!resultsTitle || !resultsList || !searchSummary) return;

    const trips = getVisibleTrips();
    const label = routeLabel();

    resultsTitle.textContent = label;
    searchSummary.textContent = `${formatDate(state.date)} · ${state.passengers} ${passengerWord(state.passengers)}`;

    resultsList.innerHTML = trips.map(t => `
      <article class="result-card">
        <div class="result-card__top">
          <div>
            <p class="route-title">${label}</p>
            <div class="pill-row">${pillsHTML(t.service)}</div>
          </div>
          <div class="price-box">
            <strong>$${formatPrice(t.price)}</strong>
            <span>por pasajero</span>
          </div>
        </div>
        <div class="result-card__bottom">
          <div class="route-meta">
            <strong>${t.departureTime} → ${t.arrivalTime}</strong><br>
            <small>${t.originPoint} → ${t.destinationPoint}</small><br>
            <small>${t.duration} · ${t.availability}</small>
          </div>
          <button class="button button--primary" type="button" data-select-trip="${t.id}">
            Elegir viaje
          </button>
        </div>
      </article>
    `).join('');
  }

  /* ═══════════════════════════════════════════════════
     7. RENDER — DETAIL
     ═══════════════════════════════════════════════════ */
  function renderDetail() {
    if (!detailCard) return;

    if (!state.selectedTrip) {
      state.selectedTrip = getVisibleTrips()[0] || null;
    }
    const t = state.selectedTrip;
    if (!t) { detailCard.innerHTML = ''; return; }

    detailCard.innerHTML = `
      <div class="detail-grid">
        <div><span>Ruta</span><strong>${routeLabel()}</strong></div>
        <div><span>Fecha</span><strong>${formatDate(t.date)}</strong></div>
        <div><span>Horario</span><strong>${t.departureTime} → ${t.arrivalTime}</strong></div>
        <div><span>Duración</span><strong>${t.duration}</strong></div>
        <div><span>Origen</span><strong>${t.originPoint}</strong></div>
        <div><span>Destino</span><strong>${t.destinationPoint}</strong></div>
        <div><span>Precio</span><strong>$${formatPrice(t.price)}</strong></div>
        <div><span>Disponibilidad</span><strong>${t.availability}</strong></div>
      </div>
      <div class="detail-note">
        <strong>Wi-Fi durante todo el viaje</strong>
        <span>Conectividad mediante Starlink. Servicio directo entre ${state.origin} y ${state.destination}.</span>
      </div>
    `;
  }

  /* ═══════════════════════════════════════════════════
     8. RENDER — CONFIRMATION
     ═══════════════════════════════════════════════════ */
  function renderConfirmation() {
    if (!confirmSummary || !state.selectedTrip || !state.customer) return;

    const t = state.selectedTrip;
    const total = t.price * state.passengers;

    const msg = encodeURIComponent(
      `Hola Éxodo, quiero continuar con mi reserva de ${routeLabel()} para el ${formatDate(t.date)} a nombre de ${state.customer.fullName}. Somos ${state.passengers} ${passengerWord(state.passengers)}.`
    );

    confirmSummary.innerHTML = `
      <div><span>Viaje</span><strong>${routeLabel()}</strong></div>
      <div><span>Fecha</span><strong>${formatDate(t.date)}</strong></div>
      <div><span>Horario</span><strong>${t.departureTime} → ${t.arrivalTime}</strong></div>
      <div><span>Pasajeros</span><strong>${state.passengers}</strong></div>
      <div><span>Precio total</span><strong>$${formatPrice(total)}</strong></div>
      <div><span>Titular</span><strong>${state.customer.fullName}</strong></div>
    `;

    if (waLink) {
      waLink.href = `https://wa.me/5490000000000?text=${msg}`;
    }
  }

  /* ═══════════════════════════════════════════════════
     9. PASSENGER COUNTER
     ═══════════════════════════════════════════════════ */
  function updatePassengerCounter() {
    if (passCountLabel) {
      passCountLabel.textContent = `${state.passengers} ${passengerWord(state.passengers)}`;
    }
  }

  function buildExtraPassengers() {
    if (!extraPassEl) return;
    extraPassEl.innerHTML = '';

    for (let i = 0; i < state.extraPassengers; i++) {
      const card = document.createElement('div');
      card.className = 'extra-passenger-card';
      card.innerHTML = `
        <h4>Pasajero ${i + 2}</h4>
        <div class="form-grid">
          <label><span>Nombre y apellido</span><input type="text" placeholder="Nombre completo"></label>
          <label><span>DNI</span><input type="text" inputmode="numeric" placeholder="DNI"></label>
        </div>
      `;
      extraPassEl.appendChild(card);
    }
  }

  /* ═══════════════════════════════════════════════════
     10. SCREEN NAVIGATION
     ═══════════════════════════════════════════════════ */
  function openScreen(name) {
    tabs.forEach(tab => tab.classList.toggle('is-active', tab.dataset.screenTarget === name));
    screens.forEach(s => s.classList.toggle('is-active', s.id === `screen-${name}`));

    const flowEl = $('.booking-flow');
    if (flowEl) {
      flowEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    if (name === 'detail') renderDetail();
    if (name === 'confirmation') renderConfirmation();
  }

  function resetFlow() {
    state.origin = 'Jujuy';
    state.destination = 'Córdoba';
    state.date = '2026-08-20';
    state.passengers = 1;
    state.selectedTrip = null;
    state.customer = null;
    state.extraPassengers = 0;

    if (originSelect) originSelect.value = state.origin;
    if (destSelect) destSelect.value = state.destination;
    if (dateInput) dateInput.value = state.date;
    if (passSelect) passSelect.value = '1';
    if (passengerForm) passengerForm.reset();
    if (extraPassEl) extraPassEl.innerHTML = '';

    updatePassengerCounter();
    renderResults();
    openScreen('results');

    const inicio = $('#inicio');
    if (inicio) inicio.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function syncStateFromSearch() {
    if (originSelect) state.origin = originSelect.value;
    if (destSelect) state.destination = destSelect.value;

    if (state.origin === state.destination) {
      state.destination = state.origin === 'Jujuy' ? 'Córdoba' : 'Jujuy';
      if (destSelect) destSelect.value = state.destination;
    }

    state.date = (dateInput && dateInput.value) || '2026-08-20';
    state.passengers = passSelect ? Number(passSelect.value) : 1;
    state.selectedTrip = getVisibleTrips()[0] || null;
    state.extraPassengers = Math.max(0, state.passengers - 1);

    buildExtraPassengers();
    updatePassengerCounter();
  }

  /* ═══════════════════════════════════════════════════
     11. EVENT LISTENERS
     ═══════════════════════════════════════════════════ */

  // Search form
  if (form) {
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      syncStateFromSearch();
      renderResults();
      openScreen('results');
    });
  }

  // Swap route
  const swapBtn = $('#swap-route');
  if (swapBtn && originSelect && destSelect) {
    swapBtn.addEventListener('click', () => {
      const temp = originSelect.value;
      originSelect.value = destSelect.value;
      destSelect.value = temp;
    });
  }

  // Select trip (delegated)
  if (resultsList) {
    resultsList.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-select-trip]');
      if (!btn) return;

      const trip = getVisibleTrips().find(t => t.id === btn.dataset.selectTrip);
      if (!trip) return;

      state.selectedTrip = trip;
      openScreen('detail');
    });
  }

  // Global click delegation
  document.addEventListener('click', (e) => {
    // Screen navigation
    const goScreen = e.target.closest('[data-go-screen]');
    if (goScreen) {
      openScreen(goScreen.dataset.goScreen);
      return;
    }

    // Reset flow
    if (e.target.closest('[data-reset-flow="true"]')) {
      resetFlow();
      return;
    }

    // Scroll to search
    if (e.target.closest('[data-scroll-search="true"]')) {
      const card = $('.search-card');
      if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      return;
    }
  });

  // Passenger form
  if (passengerForm) {
    passengerForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const fd = new FormData(passengerForm);
      state.customer = {
        fullName: fd.get('fullName'),
        dni: fd.get('dni'),
        phone: fd.get('phone'),
        email: fd.get('email')
      };
      openScreen('confirmation');
    });
  }

  // Add passenger
  if (addPassBtn) {
    addPassBtn.addEventListener('click', () => {
      if (state.extraPassengers >= 3) return; // max 4 total
      state.extraPassengers += 1;
      state.passengers = Math.max(state.passengers, state.extraPassengers + 1);
      if (passSelect) passSelect.value = String(Math.min(state.passengers, 4));
      updatePassengerCounter();
      buildExtraPassengers();
    });
  }

  // Tabs
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      openScreen(tab.dataset.screenTarget);
    });
  });

  // Mobile menu
  if (menuToggle && siteNav) {
    menuToggle.addEventListener('click', () => {
      const expanded = menuToggle.getAttribute('aria-expanded') === 'true';
      menuToggle.setAttribute('aria-expanded', String(!expanded));
      siteNav.classList.toggle('is-open', !expanded);
    });

    siteNav.addEventListener('click', (e) => {
      if (e.target.tagName === 'A' || e.target.classList.contains('nav-cta')) {
        siteNav.classList.remove('is-open');
        menuToggle.setAttribute('aria-expanded', 'false');
      }
    });
  }

  /* ═══════════════════════════════════════════════════
     12. FAQ ACCORDION
     ═══════════════════════════════════════════════════ */
  const faqDetails = $$('.faq-list details');
  faqDetails.forEach(detail => {
    detail.addEventListener('toggle', () => {
      if (detail.open) {
        faqDetails.forEach(d => {
          if (d !== detail && d.open) d.open = false;
        });
      }
    });
  });

  /* ═══════════════════════════════════════════════════
     13. SCROLL REVEAL (IntersectionObserver)
     ═══════════════════════════════════════════════════ */
  const revealObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-visible');
        revealObserver.unobserve(entry.target);
      }
    });
  }, {
    threshold: 0.08,
    rootMargin: '0px 0px -30px 0px'
  });

  $$('.reveal').forEach(el => revealObserver.observe(el));

  /* ═══════════════════════════════════════════════════
     14. INITIALIZATION
     ═══════════════════════════════════════════════════ */
  if (dateInput) {
    dateInput.value = state.date;
    // Set min date to today
    const today = new Date().toISOString().split('T')[0];
    dateInput.min = today;
  }

  renderResults();
  updatePassengerCounter();

});
