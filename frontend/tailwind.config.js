/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#f5f0ff',
          100: '#ede5ff',
          500: '#7138D8',
          600: '#6329c7',
          700: '#551fa8',
        },
      },
    },
  },
  plugins: [],
}
