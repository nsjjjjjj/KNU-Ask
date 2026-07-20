/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: { brand: {50:'#eff6ff',100:'#dbeafe',200:'#bfdbfe',500:'#3b82f6',600:'#2563eb',700:'#1d4ed8',900:'#1e3a8a'} },
      fontFamily: { sans: ['Pretendard Variable','Pretendard','Noto Sans KR','system-ui','sans-serif'] },
      boxShadow: { soft: '0 16px 45px rgba(37, 99, 235, 0.10)' },
    },
  },
  plugins: [],
}
