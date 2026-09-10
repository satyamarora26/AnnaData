import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from './App';
import Main from './compnent/Main/Main';
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

test('submits a typed query and renders the backend answer without adding a quantity', async () => {
  const query = 'How much fertilizer should I apply to wheat?';
  const answer = 'Use the dose in your soil-test recommendation or ask the local agriculture officer.';
  const originalGeolocation = navigator.geolocation;
  const originalFetch = global.fetch;
  const container = document.createElement('div');
  const root = createRoot(container);

  document.body.appendChild(container);
  Object.defineProperty(navigator, 'geolocation', {
    configurable: true,
    value: {
      getCurrentPosition: (success) => success({
        coords: { latitude: 30.9, longitude: 75.5 },
      }),
    },
  });
  global.fetch = jest.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => ({ answer }),
  });

  try {
    act(() => {
      root.render(
        <ContextProvider>
          <Main />
        </ContextProvider>
      );
    });

    const input = screen.getByPlaceholderText(/enter a prompt here/i);
    await act(async () => {
      await userEvent.type(input, `${query}{enter}`);
    });

    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));
    const [url, options] = global.fetch.mock.calls[0];
    expect(url).toMatch(/\/agent\/stream$/);
    expect(options.method).toBe('POST');
    expect(JSON.parse(options.body)).toEqual({
      query,
      history: [],
      latitude: 30.9,
      longitude: 75.5,
    });
    expect(await screen.findByText(answer)).toBeInTheDocument();
    expect(screen.queryByText(/\b\d+(?:\.\d+)?\s*(?:kg|g|ml|litres?)\b/i)).not.toBeInTheDocument();

    const followUp = 'What about irrigation?';
    await act(async () => {
      await userEvent.type(input, `${followUp}{enter}`);
    });
    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(2));
    expect(JSON.parse(global.fetch.mock.calls[1][1].body).history).toEqual([
      { role: 'user', content: query },
      { role: 'assistant', content: answer },
    ]);
  } finally {
    act(() => root.unmount());
    container.remove();
    global.fetch = originalFetch;
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: originalGeolocation,
    });
  }
});
