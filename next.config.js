// File: stephaneavril/leo_api/LEO_API-7337efa878c219546557704899d19e82342974a0/next.config.js
/** @type {import('next').NextConfig} */
// const path = require('path'); // Remueve o comenta esta línea

const nextConfig = {
  output: 'standalone',
  // Remueve o comenta todo el bloque 'webpack' por ahora
  // webpack: (config) => {
  //   config.resolve.alias['@'] = path.join(__dirname, '');
  //   return config;
  // },
};

module.exports = nextConfig;