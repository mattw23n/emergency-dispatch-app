const js = require("@eslint/js");

module.exports = [
  js.configs.recommended,
  {
    rules: {
      "indent": ["error", 2],
      "quotes": ["error", "double"],
      "semi": ["error", "always"],
      "no-unused-vars": ["warn"],
      "no-console": "off"
    },
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "commonjs",
      globals: {
        "console": "readonly",
        "process": "readonly",
        "require": "readonly",
        "module": "readonly",
        "__dirname": "readonly",
        "exports": "readonly"
      }
    }
  }
];
