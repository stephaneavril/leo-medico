// next.config.js
/** @type {import('next').NextConfig} */
const isProd = process.env.NODE_ENV === 'production';

module.exports = {
  output: 'standalone',
  basePath: isProd ? '/frontend' : '',
  assetPrefix: isProd ? '/frontend/' : '',
  // …otras opciones que tengas
};
