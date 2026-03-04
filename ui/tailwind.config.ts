import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        ink: "#0d1b1e",
        mist: "#eef4ef",
        ember: "#ff7a18",
        lagoon: "#38b6a3",
        pine: "#17352f",
        sand: "#f3ddc2",
        fern: "#8ca19a",
      },
      boxShadow: {
        glow: "0 20px 60px rgba(9, 27, 31, 0.18)",
      },
      backgroundImage: {
        mesh:
          "radial-gradient(circle at top left, rgba(255, 122, 24, 0.2), transparent 28%), radial-gradient(circle at 85% 15%, rgba(56, 182, 163, 0.18), transparent 24%), linear-gradient(180deg, #f8f4ec 0%, #eff4ef 48%, #e7efe9 100%)",
      },
    },
  },
  plugins: [],
};

export default config;
