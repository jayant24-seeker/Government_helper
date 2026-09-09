/**
 * animations.js — CitizenConnect shared background animations
 * Fireflies + connecting particles, extracted from all page templates.
 * Single source of truth: include once per page, zero duplication.
 */

(function () {
  'use strict';

  // ── Utility ──────────────────────────────────────────────────────────────

  /** Debounce: prevent resize from firing hundreds of times per second */
  function debounce(fn, delay) {
    let timer;
    return function (...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), delay);
    };
  }

  // ── Fireflies ─────────────────────────────────────────────────────────────

  const firefliesCanvas = document.getElementById('fireflies');
  if (firefliesCanvas) {
    const ctx = firefliesCanvas.getContext('2d');
    const fireflies = [];
    const COUNT = 40;

    function resizeFireflies() {
      firefliesCanvas.width  = window.innerWidth;
      firefliesCanvas.height = window.innerHeight;
    }

    class Firefly {
      constructor() { this.reset(); }
      reset() {
        this.x      = Math.random() * firefliesCanvas.width;
        this.y      = Math.random() * firefliesCanvas.height;
        this.size   = Math.random() * 3 + 2;
        this.speedX = Math.random() - 0.5;       // range: -0.5 → +0.5
        this.speedY = Math.random() - 0.5;
        this.alpha  = Math.random();
      }
      update() {
        this.x += this.speedX;
        this.y += this.speedY;
        if (this.x < 0 || this.x > firefliesCanvas.width)  this.speedX *= -1;
        if (this.y < 0 || this.y > firefliesCanvas.height) this.speedY *= -1;
        this.alpha += Math.random() * 0.1 - 0.05;
        this.alpha  = Math.max(0.3, Math.min(this.alpha, 1));
      }
      draw() {
        ctx.beginPath();
        ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
        ctx.fillStyle   = `rgba(225,177,44,${this.alpha})`;
        ctx.shadowColor = 'rgba(225,177,44,0.8)';
        ctx.shadowBlur  = 15;
        ctx.fill();
      }
    }

    resizeFireflies();
    for (let i = 0; i < COUNT; i++) fireflies.push(new Firefly());

    window.addEventListener('resize', debounce(resizeFireflies, 150));

    (function animateFireflies() {
      ctx.clearRect(0, 0, firefliesCanvas.width, firefliesCanvas.height);
      for (const f of fireflies) { f.update(); f.draw(); }
      requestAnimationFrame(animateFireflies);
    })();
  }

  // ── Connecting Particles ──────────────────────────────────────────────────

  const particlesCanvas = document.getElementById('connecting-particles');
  if (particlesCanvas) {
    const ctx   = particlesCanvas.getContext('2d');
    const parts  = [];
    const COUNT  = 70;
    const DIST_SQ = 120 * 120;   // squared — avoids sqrt in the hot loop

    function resizeParticles() {
      particlesCanvas.width  = window.innerWidth;
      particlesCanvas.height = window.innerHeight;
    }

    class Particle {
      constructor() {
        this.x      = Math.random() * particlesCanvas.width;
        this.y      = Math.random() * particlesCanvas.height;
        this.size   = Math.random() * 1.5 + 1;
        this.speedX = Math.random() * 0.6 - 0.3;
        this.speedY = Math.random() * 0.6 - 0.3;
      }
      update() {
        this.x += this.speedX;
        this.y += this.speedY;
        if (this.x < 0 || this.x > particlesCanvas.width)  this.speedX *= -1;
        if (this.y < 0 || this.y > particlesCanvas.height) this.speedY *= -1;
      }
      draw() {
        ctx.fillStyle = '#948979';
        ctx.beginPath();
        ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    resizeParticles();
    for (let i = 0; i < COUNT; i++) parts.push(new Particle());

    window.addEventListener('resize', debounce(resizeParticles, 150));

    (function animateParticles() {
      ctx.clearRect(0, 0, particlesCanvas.width, particlesCanvas.height);

      for (let i = 0; i < parts.length; i++) {
        parts[i].update();
        parts[i].draw();

        // Connection lines — use squared distance, skip sqrt
        for (let j = i + 1; j < parts.length; j++) {
          const dx = parts[i].x - parts[j].x;
          const dy = parts[i].y - parts[j].y;
          const dSq = dx * dx + dy * dy;
          if (dSq < DIST_SQ) {
            const alpha = 1 - Math.sqrt(dSq) / 120;
            ctx.strokeStyle = `rgba(148,137,121,${alpha})`;
            ctx.lineWidth   = 0.5;
            ctx.beginPath();
            ctx.moveTo(parts[i].x, parts[i].y);
            ctx.lineTo(parts[j].x, parts[j].y);
            ctx.stroke();
          }
        }
      }

      requestAnimationFrame(animateParticles);
    })();
  }
})();
