/** @type {import('next').NextConfig} */
const path = require('path'); // Add this line at the top

const nextConfig = {
  output: 'standalone',
  // Add this webpack configuration block
  webpack: (config) => {
    // This maps '@' to your project's root directory
    config.resolve.alias['@'] = path.join(__dirname, '');
    return config;
  },
};

module.exports = nextConfig;