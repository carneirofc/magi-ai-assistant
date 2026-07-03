/**
 * Tailwind v4 runs as a PostCSS plugin. The design tokens + component utilities
 * come from `@carneirofc/ui/styles.css` (imported in globals.css); Tailwind
 * generates the atomic utilities the UI-kit components and our pages reference.
 */
const config = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};

export default config;
