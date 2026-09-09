import { render, screen } from '@testing-library/react';
import App from './App';
import ContextProvider from './Context/ContextProvider';

test('renders the AnnaData assistant', () => {
  render(
    <ContextProvider>
      <App />
    </ContextProvider>
  );

  expect(screen.getByRole('heading', { name: /annadata/i })).toBeInTheDocument();
});
