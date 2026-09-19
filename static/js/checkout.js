/* ═══════════════════════════════════════════════════════
   SC VIAJES — Public Checkout & Seat Selection JS (Local)
   ═══════════════════════════════════════════════════════ */

'use strict';

document.addEventListener('DOMContentLoaded', () => {

  /* ═══════════════════════════════════════════════════
     1. TRIP TYPE TOGGLE (ONEWAY vs ROUNDTRIP)
     ═══════════════════════════════════════════════════ */
  const tripTypeRadios = document.querySelectorAll('input[name="trip_type"]');
  const returnDateContainers = [
    document.getElementById('return-date-container'),
    document.getElementById('hero-return-date-container')
  ].filter(Boolean);

  function syncTripType(val) {
    const isRoundtrip = (val === 'roundtrip');
    returnDateContainers.forEach(container => {
      container.style.display = isRoundtrip ? 'block' : 'none';
      const input = container.querySelector('input[type="date"]');
      if (input) {
        input.required = isRoundtrip;
        if (!isRoundtrip) input.value = '';
      }
    });
  }

  tripTypeRadios.forEach(radio => {
    radio.addEventListener('change', (e) => {
      syncTripType(e.target.value);
    });
    if (radio.checked) {
      syncTripType(radio.value);
    }
  });

  /* ═══════════════════════════════════════════════════
     2. SEAT SELECTION LIMITER & COUNTERS
     ═══════════════════════════════════════════════════ */
  const passengersCountInput = document.getElementById('id_passengers_count');
  const passengersLimit = passengersCountInput ? parseInt(passengersCountInput.value, 10) : 1;

  // Outbound seats
  const outboundCheckboxes = document.querySelectorAll('.outbound-seat-cb');
  const outboundCountDisplay = document.getElementById('outbound-count-display');
  const outboundPill = document.getElementById('outbound-counter-pill');

  function updateOutboundCount() {
    const checkedBoxes = document.querySelectorAll('.outbound-seat-cb:checked');
    const count = checkedBoxes.length;
    if (outboundCountDisplay) {
      outboundCountDisplay.textContent = count;
    }
    if (outboundPill) {
      outboundPill.classList.toggle('is-complete', count === passengersLimit);
    }
  }

  outboundCheckboxes.forEach(cb => {
    cb.addEventListener('change', (e) => {
      const checkedBoxes = document.querySelectorAll('.outbound-seat-cb:checked');
      if (checkedBoxes.length > passengersLimit) {
        e.preventDefault();
        cb.checked = false;
        alert(`Solo podés seleccionar hasta ${passengersLimit} butaca(s) para este viaje.`);
      }
      updateOutboundCount();
    });
  });
  updateOutboundCount();

  // Return seats
  const returnCheckboxes = document.querySelectorAll('.return-seat-cb');
  const returnCountDisplay = document.getElementById('return-count-display');
  const returnPill = document.getElementById('return-counter-pill');

  function updateReturnCount() {
    const checkedBoxes = document.querySelectorAll('.return-seat-cb:checked');
    const count = checkedBoxes.length;
    if (returnCountDisplay) {
      returnCountDisplay.textContent = count;
    }
    if (returnPill) {
      returnPill.classList.toggle('is-complete', count === passengersLimit);
    }
  }

  returnCheckboxes.forEach(cb => {
    cb.addEventListener('change', (e) => {
      const checkedBoxes = document.querySelectorAll('.return-seat-cb:checked');
      if (checkedBoxes.length > passengersLimit) {
        e.preventDefault();
        cb.checked = false;
        alert(`Solo podés seleccionar hasta ${passengersLimit} butaca(s) para el viaje de regreso.`);
      }
      updateReturnCount();
    });
  });
  updateReturnCount();

  // Form submission validation
  const checkoutForm = document.getElementById('checkout-main-form');
  if (checkoutForm) {
    checkoutForm.addEventListener('submit', (e) => {
      const outChecked = document.querySelectorAll('.outbound-seat-cb:checked');
      if (outChecked.length !== passengersLimit) {
        e.preventDefault();
        alert(`Por favor seleccioná exactamente ${passengersLimit} butaca(s) para el viaje de ida.`);
        const seatSection = document.querySelector('.seat-map-wrapper');
        if (seatSection) seatSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return;
      }

      const returnSeatsExist = document.querySelector('.return-seat-cb');
      if (returnSeatsExist) {
        const retChecked = document.querySelectorAll('.return-seat-cb:checked');
        if (retChecked.length !== passengersLimit) {
          e.preventDefault();
          alert(`Por favor seleccioná exactamente ${passengersLimit} butaca(s) para el viaje de regreso.`);
          return;
        }
      }
    });
  }

  /* ═══════════════════════════════════════════════════
     3. COUNTDOWN TIMER ON SUMMARY PAGE
     ═══════════════════════════════════════════════════ */
  const timerBox = document.getElementById('timer-box');
  const timerDisplay = document.getElementById('timer-display');

  if (timerBox && timerDisplay) {
    let remaining = parseInt(timerBox.dataset.remaining, 10);

    function pad(n) {
      return n < 10 ? '0' + n : n;
    }

    function renderTimer() {
      if (remaining <= 0) {
        timerDisplay.textContent = '00:00';
        timerBox.classList.add('timer-expired');
        // Recargar la página para que el backend ejecute expire_booking
        setTimeout(() => {
          window.location.reload();
        }, 1500);
        return;
      }

      const m = Math.floor(remaining / 60);
      const s = remaining % 60;
      timerDisplay.textContent = `${pad(m)}:${pad(s)}`;
    }

    renderTimer();

    const intervalId = setInterval(() => {
      remaining--;
      if (remaining <= 0) {
        clearInterval(intervalId);
        renderTimer();
      } else {
        renderTimer();
      }
    }, 1000);
  }

});
