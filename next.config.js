// next.config.js
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  assetPrefix: '/frontend/', // Change this if your app is in a subdirectory on Render
};

module.exports = nextConfig;