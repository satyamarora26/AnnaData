### AnnaData-Frontend

This repository contains the frontend code for the AnnaData project. It is built using modern web technologies to provide a responsive and dynamic user interface.

### Technologies Used

  * **JavaScript:** Core language for the application's logic.
  * **CSS:** Styling for the user interface.
  * **HTML:** Structure of the web pages.
  * **Tailwind CSS:** A utility-first CSS framework for rapid UI development.
  * **npm:** Package manager for installing dependencies.

### Local Setup

To get a local copy up and running, follow these simple steps.

#### Prerequisites

Make sure you have [Node.js](https://nodejs.org/en/) and npm installed on your machine. You can check if they are installed by running the following commands in your terminal:

```sh
node -v
npm -v
```

#### Installation

1.  **Clone the repository:**

    ```sh
    git clone https://github.com/ShahilGuptaa/AnnaData-Frontend.git
    ```

2.  **Navigate into the project directory:**

    ```sh
    cd AnnaData-Frontend
    ```

3.  **Install the project dependencies:**

    ```sh
    npm install
    ```

3.  **Build the project:**

    ```sh
    npm run build
    ```

4.  **Setup Environment Variables**
    You will need to create a `.env` file in the frontend project root. \\
    Copy `.env.example` to `.env`. It sets REACT_APP_API_URL=http://127.0.0.1:8000 (change the port if your backend runs elsewhere).

#### Running the Application

After the installation is complete, you can start the development server to view the application in your browser.

```sh
npm run start
```

This command will typically start a local server and open the application in your default web browser at a URL like `http://localhost:3000`.

### License

This project is open-sourced under the [MIT License](https://www.google.com/search?q=https://github.com/ShahilGuptaa/AnnaData-Frontend/blob/main/LICENSE) - see the `LICENSE` file for details.

