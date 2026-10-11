// Tailwind builds web/app.css from the classes used in these files.
// After changing classes in the UI, rebuild with: scripts/build-css.sh
module.exports = {
  content: ['./index.html', './web/app.js', './web/login.html', './web/login.js'],
  theme: { extend: {} },
  plugins: [require('@tailwindcss/typography')],
};
