/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone', // Enables standalone build for smaller Docker images/better performance on Render
};
module.exports = nextConfig;