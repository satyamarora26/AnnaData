import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { screen } from '@testing-library/react';
import App from './App';
import ContextProvider from './Context/ContextProvider';

test('renders the AnnaData assistant', () => {
  const container = document.createElement('div');
  const root = createRoot(container);
  document.body.appendChild(container);

  act(() => {
    root.render(
      <ContextProvider>
        <App />
      </ContextProvider>
    );
  });

  expect(screen.getByRole('heading', { name: /annadata/i })).toBeInTheDocument();

  act(() => root.unmount());
  container.remove();
});
